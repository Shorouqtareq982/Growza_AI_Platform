"""
Career Builder Router
Final version:
- analyze
- confirm-skills
- confirm-time
- generate-plan
- regenerate-plan
- save-plan
"""

from fastapi import APIRouter, HTTPException, UploadFile, File, Form, Depends
from uuid import UUID
import logging

from features.career_builder.schemas.career_schemas import (
    ConfirmSkillsRequest,
    ConfirmTimeRequest,
    PlanGenerateRequest,
    PlanRegenerateRequest,
    SavePlanRequest,
)
from features.career_builder.services.career_analysis_service import CareerAnalysisService
from features.career_builder.services.plan_generation_service import PlanGenerationService
from features.career_builder.services.plan_regeneration_service import PlanRegenerationService
from features.career_builder.services.plan_persistence_service import PlanPersistenceService
from features.career_builder.services.time_guidance_service import TimeGuidanceService
from features.career_builder.ml_models.advanced_realism_checker import AdvancedRealismChecker
from features.career_builder.repositories.career_repository import CareerRepository
from shared.helpers.document_parser import DocumentParser
from shared.providers.supabase.database import db as supabase_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/career", tags=["career-builder"])

LEVEL_VALUES = {
    "none": 0,
    "beginner": 1,
    "intermediate": 2,
    "advanced": 3,
}


def _calculate_gap_score_simple(current_level: str, required_level: str) -> float:
    current = LEVEL_VALUES.get((current_level or "none").lower(), 0)
    required = LEVEL_VALUES.get((required_level or "beginner").lower(), 1)

    if current >= required:
        return 0.0

    gap = required - current
    max_gap = required
    return gap / max_gap if max_gap > 0 else 1.0


def _derive_status(current_level: str, required_level: str) -> str:
    current_value = LEVEL_VALUES.get((current_level or "none").lower(), 0)
    required_value = LEVEL_VALUES.get((required_level or "beginner").lower(), 1)

    if current_value == 0:
        return "missing"
    elif current_value < required_value:
        return "partial"
    return "has"


def _recalculate_gap_fields(gap: dict) -> dict:
    current_level = (gap.get("current_level") or "none").lower()
    required_level = (gap.get("required_level") or "beginner").lower()

    gap["status"] = _derive_status(current_level, required_level)
    gap["gap_score"] = round(_calculate_gap_score_simple(current_level, required_level), 3)
    return gap


def _recalculate_reviewable_skill(skill: dict) -> dict:
    detected_level = (skill.get("detected_level") or "none").lower()
    required_level = (skill.get("required_level") or "beginner").lower()

    new_status = _derive_status(detected_level, required_level)
    skill["status"] = new_status
    skill["selected_by_default"] = new_status in ("missing", "partial")
    return skill


def _classify_study_intensity(hours: int) -> str:
    if hours <= 5:
        return "light"
    elif hours <= 10:
        return "moderate"
    return "intensive"


def _suggest_target_level(
    current_level: str,
    required_level: str,
    requested_weeks: int,
    available_hours_per_week: int
) -> tuple[str, str]:
    """
    Rule-based target decision.
    The backend decides the target level, not the user.
    """

    current_level = (current_level or "none").lower()
    required_level = (required_level or "beginner").lower()

    current_value = LEVEL_VALUES.get(current_level, 0)
    required_value = LEVEL_VALUES.get(required_level, 1)
    intensity = _classify_study_intensity(available_hours_per_week)

    # Missing skill
    if current_value == 0:
        if requested_weeks >= 12 and available_hours_per_week >= 10:
            return "intermediate", "Enough time is available to move beyond fundamentals."
        if requested_weeks >= 6:
            return "beginner", "This skill is missing, so starting with fundamentals is the best path."
        return "beginner", "Limited time makes a foundation-first target more realistic."

    # Below required level
    if current_value < required_value:
        if required_level == "advanced" and intensity == "light" and requested_weeks < 10:
            return "intermediate", "Time is limited, so reaching intermediate first is more realistic."
        return required_level, "This target is needed to meet the track requirement."

    # Already at or above required level -> optional level-up
    if current_level == "beginner":
        if requested_weeks >= 8 and available_hours_per_week >= 6:
            return "intermediate", "There is enough time to strengthen this skill to intermediate."
        return "beginner", "Your current level is enough for the available time."

    if current_level == "intermediate":
        if requested_weeks >= 10 and available_hours_per_week >= 8:
            return "advanced", "There is enough time to push this skill toward advanced."
        return "intermediate", "Your current level is already strong for the available time."

    return "advanced", "Your current level is already sufficient."


def get_repository() -> CareerRepository:
    return CareerRepository(supabase_db)


def get_analysis_service() -> CareerAnalysisService:
    repo = get_repository()
    return CareerAnalysisService(repository=repo)


def get_plan_generation_service() -> PlanGenerationService:
    repo = get_repository()
    analysis_service = CareerAnalysisService(repository=repo)
    return PlanGenerationService(repository=repo, analysis_service=analysis_service)


def get_plan_regeneration_service() -> PlanRegenerationService:
    return PlanRegenerationService()


def get_plan_persistence_service() -> PlanPersistenceService:
    repo = get_repository()
    return PlanPersistenceService(repository=repo)


def get_time_guidance_service() -> TimeGuidanceService:
    repo = get_repository()
    return TimeGuidanceService(repository=repo)


def get_advanced_realism_checker() -> AdvancedRealismChecker:
    return AdvancedRealismChecker()


@router.get("/tracks")
async def get_tracks(repo: CareerRepository = Depends(get_repository)):
    try:
        tracks = await repo.get_all_tracks() or []
        formatted_tracks = [
            {
                "track_id": track["track_id"],
                "track_name": track["track_name"],
                "description": track.get("description")
            }
            for track in tracks
        ]
        return {
            "status": "success",
            "tracks": formatted_tracks,
            "total": len(formatted_tracks)
        }
    except Exception as e:
        logger.error(f"Get tracks failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/regeneration-intents")
async def get_regeneration_intents():
    """Get available plan regeneration intents/feedback types for the UI"""
    try:
        from features.career_builder.services.plan_feedback_mapper import PlanFeedbackMapper
        
        intents = PlanFeedbackMapper.get_all_intents_for_ui()
        return {
            "status": "success",
            "available_intents": intents
        }
    except Exception as e:
        logger.error(f"Get regeneration intents failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/analyze")
async def analyze_cv(
    cv_file: UploadFile = File(...),
    track_id: int = Form(...),
    service: CareerAnalysisService = Depends(get_analysis_service),
):
    try:
        repo = service.repo
        parser = DocumentParser()

        cv_text, parsed_cv = await parser.parse_cv(file=cv_file)

        if not cv_text:
            raise HTTPException(status_code=400, detail="Failed to extract text from CV.")

        cv_id = await repo.save_cv(
            file_url=cv_file.filename,
            text_content=cv_text,
            parsed_content=parsed_cv or {}
        )

        if not cv_id:
            raise HTTPException(status_code=500, detail="Failed to save CV.")

        analysis = await service.analyze_cv_for_track(
            cv_id=cv_id,
            track_id=track_id,
            requested_weeks=0,
            user_level=None
        )

        await repo.save_analysis_cache(
            cv_id=cv_id,
            track_id=track_id,
            analysis_data={
                "cv_id": str(cv_id),
                "track_id": track_id,
                "track_name": analysis.track_name,
                "detected_level": analysis.detected_level,
                "required_level": analysis.required_level,
                "level_confidence": analysis.level_confidence,
                "level_reasoning": analysis.level_reasoning,
                "cv_skills": analysis.cv_skills,
                "matched_skills": analysis.matched_skills,
                "missing_skills": analysis.missing_skills,
                "match_percentage": analysis.match_percentage,
                "matching_method": analysis.matching_method,
                "skill_gaps": analysis.skill_gaps,
                "fit_analysis": analysis.fit_analysis,
                "reviewable_skills": analysis.reviewable_skills,
                "detected_skill_levels": analysis.detected_skill_levels,
                "analysis_quality": analysis.analysis_quality,
            }
        )

        recommended_skills = []
        owned_skills = []

        for gap in analysis.skill_gaps:
            status = gap.get("status")
            if status in ("missing", "partial"):
                recommended_skills.append({
                    "skill_id": gap.get("skill_id"),
                    "skill_name": gap.get("skill_name"),
                    "status": status,
                    "required_level": gap.get("required_level"),
                    "required_weeks": gap.get("required_weeks", 4),
                    "importance": gap.get("importance_weight", 3),
                    "selected_by_default": True
                })

        for skill in analysis.reviewable_skills:
            if skill.get("status") == "has":
                owned_skills.append({
                    "skill_id": skill.get("skill_id"),
                    "skill_name": skill.get("skill_name"),
                    "detected_level": skill.get("detected_level"),
                    "confidence": skill.get("confidence"),
                    "needs_user_input": skill.get("needs_user_input"),
                    "required_level": skill.get("required_level")
                })

        return {
            "status": "success",
            "cv_id": str(cv_id),
            "track_id": track_id,
            "track_name": analysis.track_name,
            "detected_level": analysis.detected_level,
            "required_level": analysis.required_level,
            "level_confidence": round(analysis.level_confidence, 3),
            "level_reasoning": analysis.level_reasoning,
            "recommended_skills": recommended_skills,
            "owned_skills": owned_skills,
            "summary": {
                "already_have": len(owned_skills),
                "need_to_learn": len(recommended_skills),
                "match_percentage": analysis.match_percentage
            },
            "raw": {
                "fit_analysis": analysis.fit_analysis,
                "reviewable_skills": analysis.reviewable_skills,
                "skill_gaps": analysis.skill_gaps,
                "detected_skill_levels": analysis.detected_skill_levels,
                "matched_skills": analysis.matched_skills,
                "missing_skills": analysis.missing_skills,
                "metadata": {
                    "matching_method": analysis.matching_method,
                    "analysis_quality": analysis.analysis_quality,
                }
            }
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Analyze value error: {e}", exc_info=True)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm-skills")
async def confirm_skills(
    request: ConfirmSkillsRequest,
    repo: CareerRepository = Depends(get_repository),
):
    try:
        cached = await repo.get_analysis_cache(cv_id=request.cv_id, track_id=request.track_id)

        if not cached:
            raise HTTPException(
                status_code=400,
                detail="You must call /analyze first before /confirm-skills."
            )

        reviewable_skills = cached.get("reviewable_skills", []) or []
        skill_gaps = cached.get("skill_gaps", []) or []

        override_map = {
            override.skill_id: override.level.value
            for override in request.skill_overrides
        }

        for skill in reviewable_skills:
            skill_id = skill.get("skill_id")
            if skill_id in override_map:
                skill["detected_level"] = override_map[skill_id]
                skill["needs_user_input"] = False
                _recalculate_reviewable_skill(skill)

        for gap in skill_gaps:
            skill_id = gap.get("skill_id")
            if skill_id in override_map:
                gap["current_level"] = override_map[skill_id]
                _recalculate_gap_fields(gap)

        selected_skill_ids = request.selected_skill_ids or [
            gap.get("skill_id")
            for gap in skill_gaps
            if gap.get("status") in ("missing", "partial")
        ]

        updated_cache = {
            **cached,
            "selected_skill_ids": selected_skill_ids,
            "skill_overrides": [
                {
                    "skill_id": override.skill_id,
                    "level": override.level.value
                }
                for override in request.skill_overrides
            ],
            "reviewable_skills": reviewable_skills,
            "skill_gaps": skill_gaps,
            "skills_confirmed": True,
        }

        await repo.save_analysis_cache(
            cv_id=request.cv_id,
            track_id=request.track_id,
            analysis_data=updated_cache
        )

        return {
            "status": "success",
            "cv_id": str(request.cv_id),
            "track_id": request.track_id,
            "track_name": cached.get("track_name"),
            "detected_level": cached.get("detected_level"),
            "selected_skill_ids": selected_skill_ids,
            "reviewable_skills": reviewable_skills,
            "skill_gaps": skill_gaps,
            "fit_analysis": cached.get("fit_analysis", {}),
            "metadata": {
                "match_percentage": cached.get("match_percentage", 0),
                "matching_method": cached.get("matching_method", "unknown"),
                "analysis_quality": cached.get("analysis_quality", 0),
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Confirm skills failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm-time-preview")
async def confirm_time_preview(
    cv_id: UUID = Form(...),
    track_id: int = Form(...),
    service: TimeGuidanceService = Depends(get_time_guidance_service),
    repo: CareerRepository = Depends(get_repository),
):
    """
    Preview time guidance BEFORE user enters hours/weeks.
    This is a guide showing min/suitable/max weeks based on selected skills.
    Uses default 6 hours/week as baseline for the guidance.
    """
    try:
        cached = await repo.get_analysis_cache(cv_id=cv_id, track_id=track_id)

        if not cached:
            raise HTTPException(
                status_code=400,
                detail="You must call /analyze first before /confirm-time-preview."
            )

        skill_gaps = cached.get("skill_gaps", []) or []
        selected_skill_ids = cached.get("selected_skill_ids", []) or [
            gap.get("skill_id")
            for gap in skill_gaps
            if gap.get("status") in ("missing", "partial")
        ]
        detected_skill_levels = cached.get("detected_skill_levels", {}) or {}

        if not selected_skill_ids:
            raise HTTPException(
                status_code=400,
                detail="No skills selected. Please confirm skills first."
            )

        # Use default 6 hours/week for preview guidance
        default_hours = 6

        # Get time guidance
        guidance = await service.get_time_guidance(
            cv_id=cv_id,
            track_id=track_id,
            selected_skill_ids=selected_skill_ids,
            detected_skill_levels=detected_skill_levels,
            available_hours_per_week=default_hours
        )

        # Build guidance message
        guidance_message = (
            f"Based on {default_hours} hours/week study commitment:\n"
            f"• Minimum: {guidance.minimum_weeks} weeks (focus on essentials)\n"
            f"• Suitable: {guidance.suitable_weeks} weeks (recommended)\n"
            f"• Comprehensive: {guidance.maximum_weeks} weeks (master everything)"
        )

        return {
            "status": "success",
            "cv_id": str(cv_id),
            "track_id": track_id,
            "track_name": cached.get("track_name"),
            "detected_level": cached.get("detected_level"),
            "selected_skill_ids": selected_skill_ids,
            "guidance_hours_per_week": default_hours,
            "time_guidance": {
                "minimum_weeks": guidance.minimum_weeks,
                "suitable_weeks": guidance.suitable_weeks,
                "maximum_weeks": guidance.maximum_weeks,
                "study_intensity": guidance.study_intensity,
                "breakdown": {
                    "minimum": guidance.minimum_weeks_breakdown,
                    "suitable": guidance.suitable_weeks_breakdown,
                    "maximum": guidance.maximum_weeks_breakdown,
                }
            },
            "guidance_message": guidance_message,
            "note": "This is a preview based on default 6 hours/week. Adjust your hours/weeks in the next step for accurate validation."
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Time preview value error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Time preview failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/confirm-time")
async def confirm_time(
    request: ConfirmTimeRequest,
    repo: CareerRepository = Depends(get_repository),
    service: TimeGuidanceService = Depends(get_time_guidance_service),
):
    try:
        cached = await repo.get_analysis_cache(cv_id=request.cv_id, track_id=request.track_id)

        if not cached:
            raise HTTPException(
                status_code=400,
                detail="You must call /analyze first before /confirm-time."
            )

        detected_level = cached.get("detected_level", "beginner")
        reviewable_skills = cached.get("reviewable_skills", []) or []
        skill_gaps = cached.get("skill_gaps", []) or []
        selected_skill_ids = cached.get("selected_skill_ids", []) or [
            gap.get("skill_id")
            for gap in skill_gaps
            if gap.get("status") in ("missing", "partial")
        ]

        if not selected_skill_ids:
            raise HTTPException(
                status_code=400,
                detail="No skills selected. Please confirm skills first."
            )

        detected_skill_levels = cached.get("detected_skill_levels", {}) or {}
        if not detected_skill_levels:
            detected_skill_levels = {
                gap.get("skill_name"): (gap.get("current_level") or "none").lower()
                for gap in skill_gaps
                if gap.get("skill_name")
            }

        # 1) نفس حسبة الـ preview لكن بعدد ساعات اليوزر
        guidance = await service.get_time_guidance(
            cv_id=request.cv_id,
            track_id=request.track_id,
            selected_skill_ids=selected_skill_ids,
            detected_skill_levels=detected_skill_levels,
            available_hours_per_week=request.available_hours_per_week
        )

        minimum_weeks = guidance.minimum_weeks
        suitable_weeks = guidance.suitable_weeks
        maximum_weeks = guidance.maximum_weeks
        requested_weeks = request.requested_weeks

        # 2) classification
        warnings = []
        suggestions = []
        is_realistic = True
        adjustment = "ok"
        zone = "suitable"

        if requested_weeks < minimum_weeks:
            is_realistic = False
            adjustment = "unrealistic_too_short"
            zone = "below_minimum"
            warnings.append(
                f"Your requested {requested_weeks} weeks is below the minimum required {minimum_weeks} weeks."
            )
            suggestions.append(
                f"Increase to at least {minimum_weeks} weeks, or accept a lighter target scope."
            )

        elif requested_weeks < suitable_weeks:
            adjustment = "very_tight"
            zone = "minimum"
            warnings.append(
                f"Your {requested_weeks} weeks is above minimum but below the suitable range ({suitable_weeks} weeks)."
            )
            suggestions.append(
                "This falls in the minimum band. Focus should stay on the selected skills first."
            )

        elif requested_weeks <= maximum_weeks:
            adjustment = "ok"
            zone = "suitable"
            suggestions.append(
                "This falls in the suitable band and should support a balanced plan."
            )

        else:
            adjustment = "excessive"
            zone = "above_maximum"
            suggestions.append(
                f"Your {requested_weeks} weeks is above the maximum guidance ({maximum_weeks} weeks). You can safely expand depth or raise some targets."
            )

        if request.available_hours_per_week <= 3:
            warnings.append(
                f"Only {request.available_hours_per_week} hours/week is light. Make sure these hours are focused."
            )

        # 3) بعد الـ check فقط غيّر targets
        suggested_targets = []
        confirmed_learning_targets = []

        for gap in skill_gaps:
            skill_id = gap.get("skill_id")
            if skill_id is None:
                continue

            current_level = (gap.get("current_level") or "none").lower()
            required_level = (gap.get("required_level") or "beginner").lower()
            importance_weight = int(gap.get("importance_weight", 3) or 3)
            required_weeks = int(gap.get("required_weeks", 4) or 4)
            is_core = bool(gap.get("is_core", True))
            status = gap.get("status")

            target_level = current_level
            reason = "No target change."
            learning_mode = "level_up"

            if zone in ("below_minimum", "minimum"):
                if skill_id not in selected_skill_ids:
                    continue

                if current_level == "none":
                    target_level = "beginner"
                    reason = "Minimum scope: selected missing skill starts from fundamentals."
                    learning_mode = "learn_from_scratch"
                elif current_level == "beginner":
                    target_level = "intermediate"
                    reason = "Minimum scope: selected beginner skill should reach intermediate."
                    learning_mode = "level_up"
                else:
                    target_level = current_level
                    reason = "Current level is already enough for the minimum scope."
                    learning_mode = "level_up"

            elif zone == "suitable":
                include = (skill_id in selected_skill_ids) or (
                    status in ("has", "partial") and is_core
                )
                if not include:
                    continue

                target_level = "intermediate"
                learning_mode = "learn_from_scratch" if current_level == "none" else "level_up"

                if skill_id in selected_skill_ids:
                    reason = "Suitable scope: selected skills progress toward intermediate."
                else:
                    reason = "Suitable scope: current core skills are reinforced to intermediate."

            else:  # above_maximum
                include = (skill_id in selected_skill_ids) or (LEVEL_VALUES.get(current_level, 0) >= 1)
                if not include:
                    continue

                target_level = "advanced"
                learning_mode = "learn_from_scratch" if current_level == "none" else "level_up"

                if skill_id in selected_skill_ids:
                    reason = "Extended scope: selected skills can go deeper toward advanced."
                else:
                    reason = "Extended scope: current skills can be raised toward advanced."

            suggested_targets.append({
                "skill_id": skill_id,
                "skill_name": gap.get("skill_name"),
                "current_level": current_level,
                "suggested_target_level": target_level,
                "target_reason": reason,
                "learning_mode": learning_mode
            })

            current_value = LEVEL_VALUES.get(current_level, 0)
            target_value = LEVEL_VALUES.get(target_level, 1)

            if target_value <= current_value:
                continue

            confirmed_learning_targets.append({
                "skill_id": skill_id,
                "skill_name": gap.get("skill_name"),
                "current_level": current_level,
                "target_level": target_level,
                "required_level": required_level,
                "required_weeks": required_weeks,
                "importance_weight": importance_weight,
                "learning_mode": learning_mode
            })

        if not confirmed_learning_targets:
            raise HTTPException(
                status_code=400,
                detail="No valid learning targets found after time confirmation."
            )

        realism = {
            "is_realistic": is_realistic,
            "adjustment": adjustment,
            "zone": zone,
            "requested_weeks": requested_weeks,
            "available_hours_per_week": request.available_hours_per_week,
            "study_intensity": guidance.study_intensity,
            "calculated_minimum_weeks": minimum_weeks,
            "calculated_suitable_weeks": suitable_weeks,
            "calculated_maximum_weeks": maximum_weeks,
            "warnings": warnings,
            "suggestions": suggestions,
        }

        # Compute confirmed user's overall level from confirmed learning targets
        confirmed_level_values = []
        for target in confirmed_learning_targets:
            current_level = (target.get("current_level") or "none").lower()
            confirmed_level_values.append(LEVEL_VALUES.get(current_level, 0))
        
        confirmed_overall_level = "beginner"
        if confirmed_level_values:
            avg_level_value = sum(confirmed_level_values) / len(confirmed_level_values)
            if avg_level_value >= 2.5:
                confirmed_overall_level = "advanced"
            elif avg_level_value >= 1.5:
                confirmed_overall_level = "intermediate"
            elif avg_level_value > 0:
                confirmed_overall_level = "beginner"
            else:
                confirmed_overall_level = "none"

        updated_cache = {
            **cached,
            "available_hours_per_week": request.available_hours_per_week,
            "confirmed_requested_weeks": requested_weeks,
            "suggested_targets": suggested_targets,
            "confirmed_learning_targets": confirmed_learning_targets,
            "level_used": confirmed_overall_level,
            "realism": realism,
            "time_confirmed": True,
            "confirmed": True,
        }

        await repo.save_analysis_cache(
            cv_id=request.cv_id,
            track_id=request.track_id,
            analysis_data=updated_cache
        )

        return {
            "status": "success",
            "cv_id": str(request.cv_id),
            "track_id": request.track_id,
            "track_name": cached.get("track_name"),
            "detected_level": detected_level,
            "available_hours_per_week": request.available_hours_per_week,
            "requested_weeks": requested_weeks,
            "selected_skill_ids": selected_skill_ids,
            "suggested_targets": suggested_targets,
            "confirmed_learning_targets": confirmed_learning_targets,
            "realism": realism,
            "time_guidance": {
                "minimum_weeks": minimum_weeks,
                "suitable_weeks": suitable_weeks,
                "maximum_weeks": maximum_weeks,
                "study_intensity": guidance.study_intensity,
                "breakdown": {
                    "minimum": guidance.minimum_weeks_breakdown,
                    "suitable": guidance.suitable_weeks_breakdown,
                    "maximum": guidance.maximum_weeks_breakdown,
                }
            },
            "reviewable_skills": reviewable_skills,
            "skill_gaps": skill_gaps,
            "fit_analysis": cached.get("fit_analysis", {}),
            "metadata": {
                "match_percentage": cached.get("match_percentage", 0),
                "matching_method": cached.get("matching_method", "unknown"),
                "analysis_quality": cached.get("analysis_quality", 0),
            }
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Confirm time failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-plan")
async def generate_plan(
    request: PlanGenerateRequest,
    service: PlanGenerationService = Depends(get_plan_generation_service),
    repo: CareerRepository = Depends(get_repository),
):
    try:
        # Get cached data to retrieve confirmed user level
        cached = await repo.get_analysis_cache(cv_id=request.cv_id, track_id=request.track_id)
        confirmed_user_level = cached.get("level_used") if cached else None
        
        result = await service.generate_plan(
            cv_id=request.cv_id,
            track_id=request.track_id,
            duration_weeks=request.duration_weeks,
            available_hours_per_week=request.available_hours_per_week,
            user_level=confirmed_user_level,
            requested_weeks=request.duration_weeks
        )

        return {
            "status": "success",
            **result
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Generate plan value error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Plan generation failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/regenerate-plan")
async def regenerate_plan(
    request: PlanRegenerateRequest,
    service: PlanRegenerationService = Depends(get_plan_regeneration_service),
):
    try:
        result = await service.regenerate_plan(
            previous_plan=request.previous_plan,
            feedback_intents=request.feedback_intents,
            regeneration_mode=request.regeneration_mode
        )

        return {
            "status": "success",
            **result
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Regenerate plan value error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Plan regeneration failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/save-plan")
async def save_plan(
    request: SavePlanRequest,
    service: PlanPersistenceService = Depends(get_plan_persistence_service),
):
    try:
        result = await service.save_plan(
            user_id=request.user_id,
            cv_id=request.cv_id,
            track_id=request.track_id,
            detected_level=request.detected_level.value,
            confirmed_level=request.confirmed_level.value,
            duration_weeks=request.duration_weeks,
            plan_data={
                "available_hours_per_week": request.available_hours_per_week,
                "weekly_breakdown": [
                    {
                        "week_number": item.week_number,
                        "focus_skills": item.focus_skills,
                        "topic": item.topic,
                        "description": item.description,
                        "resources": [
                            r.model_dump() if hasattr(r, "model_dump") else r
                            for r in item.resources
                        ],
                    }
                    for item in request.weekly_content
                ]
            },
            skill_gaps=[gap.model_dump() for gap in request.skill_gaps]
        )

        return {
            "status": "success",
            **result
        }

    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"Save plan value error: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Save plan failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/diagnostic/health-check")
async def full_diagnostic():
    """
    🏥 Complete diagnostic to test all API dependencies.
    
    Tests:
    - YouTube API
    - SerpApi
    - LLM Providers (Gemini, Anthropic, OpenRouter)
    - Database (Supabase/PostgreSQL)
    - File Storage (Cloudinary, Azure)
    """
    from core.config import settings
    from features.career_builder.services.resource_search_service import ResourceSearchService
    from shared.providers.llm_models.llm_provider import create_llm_provider
    
    diagnostic_results = {
        "timestamp": str(__import__("datetime").datetime.now()),
        "overall_status": "checking...",
        "apis": {},
        "warnings": [],
        "errors": []
    }
    
    # ==========================================
    # 1️⃣ YouTube API
    # ==========================================
    try:
        service = ResourceSearchService()
        youtube_key = service.youtube_api_key
        
        if not youtube_key:
            diagnostic_results["apis"]["youtube"] = {
                "status": "⚠️ NOT CONFIGURED",
                "configured": False
            }
            diagnostic_results["warnings"].append("YouTube API key not set")
        else:
            diagnostic_results["apis"]["youtube"] = {
                "status": "✅ CONFIGURED",
                "configured": True,
                "key_preview": youtube_key[:10] + "..." + youtube_key[-5:]
            }
    except Exception as e:
        diagnostic_results["apis"]["youtube"] = {"status": "❌ ERROR", "error": str(e)}
        diagnostic_results["errors"].append(f"YouTube check failed: {e}")
    
    # ==========================================
    # 2️⃣ SerpApi
    # ==========================================
    try:
        service = ResourceSearchService()
        serpapi_key = service.serpapi_api_key
        
        if not serpapi_key:
            diagnostic_results["apis"]["serpapi"] = {
                "status": "⚠️ NOT CONFIGURED",
                "configured": False
            }
            diagnostic_results["warnings"].append("SerpApi key not set (fallback will be used)")
        else:
            # Test with a simple query
            try:
                test_results = await service._search_serpapi(
                    query="python",
                    resource_type="docs",
                    title="Python Docs"
                )
                diagnostic_results["apis"]["serpapi"] = {
                    "status": "✅ WORKING",
                    "configured": True,
                    "test_query": "python docs",
                    "results_count": len(test_results),
                    "key_preview": serpapi_key[:10] + "..." + serpapi_key[-5:]
                }
            except Exception as api_error:
                if "429" in str(api_error):
                    diagnostic_results["apis"]["serpapi"] = {
                        "status": "⚠️ RATE LIMITED (429)",
                        "configured": True,
                        "error": "API quota exceeded - fallback will be used",
                        "key_preview": serpapi_key[:10] + "..." + serpapi_key[-5:]
                    }
                    diagnostic_results["warnings"].append("SerpApi is rate limited - fallback resources will be used")
                else:
                    raise
    except Exception as e:
        diagnostic_results["apis"]["serpapi"] = {"status": "❌ ERROR", "error": str(e)[:100]}
        diagnostic_results["errors"].append(f"SerpApi check failed: {e}")
    
    # ==========================================
    # 3️⃣ LLM Providers
    # ==========================================
    try:
        llm_provider = settings.LLM_PROVIDER or "not_set"
        
        if llm_provider == "not_set":
            diagnostic_results["apis"]["llm"] = {
                "status": "❌ NOT CONFIGURED",
                "provider": None
            }
            diagnostic_results["errors"].append("LLM_PROVIDER not set in .env")
        else:
            llm_config = {
                "status": "✅ CONFIGURED",
                "provider": llm_provider,
                "keys_configured": {}
            }
            
            # Check each provider
            if llm_provider == "gemini":
                gemini_key = getattr(settings, "GEMINI_API_KEY", None)
                llm_config["keys_configured"]["gemini"] = bool(gemini_key)
                if not gemini_key:
                    diagnostic_results["errors"].append("Gemini API key not configured")
                
            elif llm_provider == "anthropic":
                anthropic_key = getattr(settings, "ANTHROPIC_API_KEY", None)
                llm_config["keys_configured"]["anthropic"] = bool(anthropic_key)
                if not anthropic_key:
                    diagnostic_results["errors"].append("Anthropic API key not configured")
                    
            elif llm_provider == "openrouter":
                openrouter_key = getattr(settings, "OPENROUTER_API_KEY", None)
                llm_config["keys_configured"]["openrouter"] = bool(openrouter_key)
                if not openrouter_key:
                    diagnostic_results["errors"].append("OpenRouter API key not configured")
            
            diagnostic_results["apis"]["llm"] = llm_config
    except Exception as e:
        diagnostic_results["apis"]["llm"] = {"status": "❌ ERROR", "error": str(e)[:100]}
        diagnostic_results["errors"].append(f"LLM check failed: {e}")
    
    # ==========================================
    # 4️⃣ Database (Supabase)
    # ==========================================
    try:
        from shared.providers.supabase.database import db as supabase_db
        
        supabase_url = settings.SUPABASE_URL
        supabase_key = settings.SUPABASE_ANON_KEY
        
        if not supabase_url or not supabase_key:
            diagnostic_results["apis"]["database"] = {
                "status": "⚠️ NOT CONFIGURED",
                "type": "supabase"
            }
            diagnostic_results["warnings"].append("Supabase not configured")
        else:
            try:
                # Try a simple query
                tables_result = supabase_db.table("tracks").select("track_id", count="exact").limit(1).execute()
                diagnostic_results["apis"]["database"] = {
                    "status": "✅ CONNECTED",
                    "type": "supabase",
                    "url_preview": supabase_url[:30] + "..."
                }
            except Exception as db_error:
                diagnostic_results["apis"]["database"] = {
                    "status": "❌ CONNECTION FAILED",
                    "type": "supabase",
                    "error": str(db_error)[:100]
                }
                diagnostic_results["errors"].append(f"Database connection failed: {db_error}")
    except Exception as e:
        diagnostic_results["apis"]["database"] = {"status": "❌ ERROR", "error": str(e)[:100]}
        diagnostic_results["errors"].append(f"Database check failed: {e}")
    
    # ==========================================
    # 5️⃣ File Storage
    # ==========================================
    storage_status = {}
    
    # Cloudinary
    try:
        cloudinary_cloud = settings.CLOUDINARY_CLOUD_NAME
        cloudinary_key = settings.CLOUDINARY_API_KEY
        
        if not cloudinary_cloud or not cloudinary_key:
            storage_status["cloudinary"] = {
                "status": "⚠️ NOT CONFIGURED"
            }
        else:
            storage_status["cloudinary"] = {
                "status": "✅ CONFIGURED",
                "cloud": cloudinary_cloud[:20] + "..."
            }
    except Exception as e:
        storage_status["cloudinary"] = {"status": "❌ ERROR", "error": str(e)[:80]}
    
    # Azure Storage
    try:
        azure_conn = settings.AZURE_STORAGE_CONNECTION_STRING
        if not azure_conn:
            storage_status["azure"] = {"status": "⚠️ NOT CONFIGURED"}
        else:
            storage_status["azure"] = {"status": "✅ CONFIGURED"}
    except Exception as e:
        storage_status["azure"] = {"status": "❌ ERROR", "error": str(e)[:80]}
    
    diagnostic_results["apis"]["storage"] = storage_status
    
    # ==========================================
    # Calculate Overall Status
    # ==========================================
    error_count = len(diagnostic_results["errors"])
    critical_missing = any(
        "not_set" in str(v).lower() or "not configured" in str(v).lower()
        for apis_dict in diagnostic_results["apis"].values()
        for v in (apis_dict.values() if isinstance(apis_dict, dict) else [])
    )
    
    if error_count > 2 or critical_missing:
        diagnostic_results["overall_status"] = "⚠️ DEGRADED"
    elif error_count > 0:
        diagnostic_results["overall_status"] = "⚠️ PARTIAL"
    else:
        diagnostic_results["overall_status"] = "✅ HEALTHY"
    
    return diagnostic_results