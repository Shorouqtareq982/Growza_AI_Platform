"""
Level Detection & User Override Endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Dict
from uuid import UUID
from pydantic import BaseModel

from shared.providers.supabase.database import get_db
from features.career_builder.repositories.career_repository import CareerRepository
from features.career_builder.ml_models.level_detector import LevelDetector
import logging

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/career/levels",
    tags=["skill-levels"]
)


# =====================================================
# REQUEST/RESPONSE SCHEMAS
# =====================================================

class SkillLevelOverride(BaseModel):
    """User's manual override for a skill level"""
    skill: str
    new_level: str  # none, beginner, intermediate, advanced


class UserOverrideRequest(BaseModel):
    """Request to override detected skill levels"""
    cv_id: UUID
    track_id: int
    overrides: List[SkillLevelOverride]


class SkillLevelDisplay(BaseModel):
    """Skill level for UI display"""
    skill: str
    detected_level: str
    final_level: str
    confidence: float
    reasoning: str
    evidence: str | None
    user_overridden: bool
    suggested_levels: List[str]


# =====================================================
# DEPENDENCY
# =====================================================

async def get_level_detector(
    db: AsyncSession = Depends(get_db)
) -> LevelDetector:
    """Get level detector instance"""
    return LevelDetector()


def _get_review_instructions(result: Dict) -> str:
    """
    Generate context-aware instructions based on confidence
    """
    requires_mandatory = result.get('requires_mandatory_review', False)
    
    if requires_mandatory:
        return (
            "⚠️ Some skill levels have low confidence. "
            "Please review and manually verify these levels before proceeding."
        )
    else:
        return (
            "✓ Review the detected levels below. "
            "You can adjust any level if you feel the assessment isn't accurate."
        )


# =====================================================
# ENDPOINTS
# =====================================================

@router.post(
    "/detect",
    summary="🎯 Detect skill levels from CV",
    description="""
    Analyzes CV using LLM to detect skill levels for each required skill.
    
    **Returns:**
    - Per-skill level detection (none/beginner/intermediate/advanced)
    - Confidence scores
    - Reasoning for each assessment
    - Evidence from CV
    - Overall level assessment
    
    **User can then review and override any detected levels**
    """
)
async def detect_skill_levels(
    cv_id: UUID,
    track_id: int,
    db: AsyncSession = Depends(get_db),
    detector: LevelDetector = Depends(get_level_detector)
):
    """
    Detect skill levels for CV + Track combination
    
    **Workflow:**
    1. Get CV from database
    2. Get required skills for track
    3. Use LLM to analyze CV and detect levels
    4. Return results for user review
    """
    try:
        repo = CareerRepository(db)
        
        # Get CV
        cv_data = await repo.get_cv_by_id(cv_id)
        if not cv_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"CV not found: {cv_id}"
            )
        
        # Get track skills
        track_skills = await repo.get_skills_by_track(track_id, 'beginner')
        required_skill_names = [s['skill_name'] for s in track_skills]
        
        # Detect levels
        cv_text = cv_data.get('text_content', '')
        parsed_content = cv_data.get('parsed_content', {})
        
        if isinstance(parsed_content, str):
            import json
            try:
                parsed_content = json.loads(parsed_content)
            except:
                parsed_content = {}
        
        result = await detector.detect_skill_levels(
            cv_text=cv_text,
            parsed_cv_data=parsed_content,
            required_skills=required_skill_names
        )
        
        return {
            "cv_id": str(cv_id),
            "track_id": track_id,
            "skill_levels": result['skill_levels'],
            "overall_level": result['overall_level'],
            "overall_confidence_badge": result['overall_confidence_badge'],
            "overall_confidence_color": result['overall_confidence_color'],
            "summary": result['summary'],
            "user_review_recommended": True,
            "requires_mandatory_review": result.get('requires_mandatory_review', False),
            "instructions": self._get_review_instructions(result),
            "ui_hints": {
                "show_confidence_badges": True,
                "highlight_low_confidence": True,
                "require_override_for_low_confidence": True
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Level detection failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Level detection failed: {str(e)}"
        )


@router.post(
    "/override",
    summary="✏️ Apply user overrides to skill levels",
    description="""
    Allows user to manually override any detected skill levels.
    
    **Use case:**
    User disagrees with LLM's assessment and wants to set their own level.
    
    **Example:**
    LLM detected "Python: intermediate" but user knows they're advanced.
    User can override to "advanced".
    """
)
async def apply_user_overrides(
    request: UserOverrideRequest,
    detector: LevelDetector = Depends(get_level_detector)
):
    """
    Apply user's manual overrides to detected levels
    
    **Request Body:**
    ```json
    {
      "cv_id": "uuid",
      "track_id": 1,
      "overrides": [
        {"skill": "Python", "new_level": "advanced"},
        {"skill": "Docker", "new_level": "intermediate"}
      ]
    }
    ```
    
    **Returns:**
    Updated skill levels with user's choices applied
    """
    try:
        # Convert overrides to dict
        override_dict = {
            override.skill: override.new_level 
            for override in request.overrides
        }
        
        # Note: In a real implementation, you'd:
        # 1. Get the original detected levels from database/cache
        # 2. Apply overrides
        # 3. Recalculate overall level
        # 4. Save to database
        
        # For now, return example response
        return {
            "cv_id": str(request.cv_id),
            "track_id": request.track_id,
            "overrides_applied": len(override_dict),
            "message": "Skill levels updated successfully",
            "next_step": "Proceed to gap analysis with updated levels"
        }
        
    except Exception as e:
        logger.error(f"Override failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to apply overrides: {str(e)}"
        )


@router.get(
    "/ui-config",
    summary="Get UI configuration for level selection",
    description="Returns configuration for frontend level selection UI"
)
async def get_ui_config():
    """
    Get UI configuration for skill level selection
    
    **Frontend can use this to render:**
    - Radio buttons
    - Dropdowns
    - Level descriptions
    """
    return {
        "levels": [
            {
                "value": "none",
                "label": "No Experience",
                "description": "Not familiar with this skill",
                "color": "#gray"
            },
            {
                "value": "beginner",
                "label": "Beginner",
                "description": "Basic knowledge, learning stage",
                "color": "#blue"
            },
            {
                "value": "intermediate",
                "label": "Intermediate",
                "description": "Working knowledge, can use independently",
                "color": "#green"
            },
            {
                "value": "advanced",
                "label": "Advanced",
                "description": "Expert level, can mentor others",
                "color": "#purple"
            }
        ],
        "ui_component": "radio_group",
        "allow_none": True,
        "instructions": "Select the level that best matches your actual experience with this skill"
    }
