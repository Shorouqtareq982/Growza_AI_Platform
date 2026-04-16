"""
Plan Generation Service - FULLY DYNAMIC VERSION
All plan generation is context-aware and user-specific:
1. Pre-allocates weeks per skill based on importance + current level + target level
2. Generates subtopic guides dynamically (no hardcoded skill maps)
3. Creates personalized study guides with time splits
4. Ensures resources match user's actual level progression
5. Respects track, timeframe, and availability constraints
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional
from uuid import UUID

from features.career_builder.repositories.career_repository import CareerRepository
from features.career_builder.services.career_analysis_service import CareerAnalysisService
from features.career_builder.services.resource_search_service import ResourceSearchService
from shared.providers.llm_models.llm_provider import create_llm_provider

logger = logging.getLogger(__name__)


# ============================================================
# DYNAMIC PROMPT — Skill-by-skill schedule with study guides
# ============================================================

PLAN_GENERATION_PROMPT = """
You are a senior software engineering mentor designing a personalized weekly learning plan.

You have been given a SKILL SCHEDULE with pre-allocated weeks per skill.
Your job: fill in concrete, progressive weekly content following that schedule exactly.

==============================================================
CRITICAL: USER'S CONFIRMED SKILL LEVELS
==============================================================
The "current_level" in each skill entry is the USER'S CONFIRMED level from their skill confirmations.
This is NOT a guess — the user has explicitly confirmed their current proficiency for each skill.
Use these confirmed levels to:
1. Choose resource quality/difficulty (no beginner content if user is intermediate)
2. Skip redundant basics if user already knows the skill
3. Match study guide complexity to their actual level
4. Ensure resource_queries target their exact current level

==============================================================
ABSOLUTE RULES:
==============================================================
1. Follow the SKILL SCHEDULE exactly — same week numbers, same skill, same week count
2. Each week MUST cover the suggested "subtopic_focus" (you may refine it, not ignore it)
3. Week topics must be SPECIFIC: name the exact concept, tool, or technique
4. Descriptions must say what the user DOES: which tool, which exercise, what output
5. Within each skill's block, weeks must BUILD on each other — no repetition
6. Return VALID JSON only
7. NO URLs — only resource_queries (title + query + type)

==============================================================
CRITICAL RULE: WEEK 1 LEVEL FOR MISSED SKILLS (learning_mode=learn_from_scratch)
==============================================================
IF a skill has learning_mode=\"learn_from_scratch\" (user missed this skill entirely):
  • Week 1 MUST be beginner-level ONLY
  • Week 1 topic MUST be fundamentals, core concepts, mental model setup
  • Week 1 expected_level_after_week MUST be \"beginner\" (NOT intermediate/advanced)
  • NO advanced patterns, optimization, or production techniques in week 1
  • FORBIDDEN: \"Intermediate patterns: beyond the basics\" as week 1 topic for missed skills
  • Each skill must START from zero and PROGRESS gradually across allocated weeks

EXAMPLE WRONG: \"Pandas: Intermediate patterns: beyond the basics\" in week 1 for Pandas if missed
EXAMPLE CORRECT: \"Pandas: Fundamentals and DataFrame basics\" in week 1, then \"Intermediate patterns\" in week 3+

==============================================================
STUDY GUIDE REQUIREMENT:
==============================================================
Every week MUST include a "study_guide" object:
{{
  "study_guide": {{
    "what_to_study": ["main concept or API", "secondary concept"],
    "how_to_study": [
      "Step 1: read/watch about X",
      "Step 2: practice Y with concrete exercise",
      "Step 3: apply to Z"
    ],
    "time_split": {{
      "reading_study": "25%",
      "hands_on_coding": "50%",
      "project_integration": "25%"
    }}
  }}
}}

how_to_study should be CONCRETE and ACTIONABLE:
  BAD:  "Study the topic"
  GOOD: "Open pandas docs on groupby, then do concrete exercises on grouped analysis"

==============================================================
RESOURCE RULES:
==============================================================
Every week MUST have 4 resource_queries with MIXED types:
  - 1x "youtube" — use technique_keywords in the query
  - 1x "docs" OR "article"
  - 1x "practice" OR "project"
  - 1x supplementary type

CRITICAL: Match resources to the USER'S CONFIRMED current_level, not generic skill name
If user is intermediate, avoid "beginner tutorial" resources
If user is advanced, seek professional/production-level resources

==============================================================
PROGRESSION RULES:
==============================================================
- none → beginner: Week 1 of skill = "beginner" MAX
- beginner → intermediate: Week 2-3 of skill earliest
- intermediate → advanced: Week 3+ of skill only
- learning_mode="level_up": Skip basics, go deeper immediately
- learning_mode="learn_from_scratch": Start from zero and build gradually

==============================================================
USER CONTEXT:
==============================================================
Track: {track_name}
Hours Available Per Week: {available_hours_per_week} ({study_intensity})
Total Plan Duration: {duration_weeks} weeks
Planning Mode: {planning_mode}
User's Overall Confirmed Level: {current_average_level}
Expected Final Level After Plan: {final_expected_level}

==============================================================
SKILL SCHEDULE — FOLLOW THIS EXACTLY:
==============================================================
{skill_schedule_json}

==============================================================
OUTPUT JSON:
==============================================================
{{
  "plan_summary": "Updated summary",
  "improvement_summary": "Updated improvement sentence",
  "weekly_breakdown": [
    {{
      "week_number": 1,
      "focus_skills": ["SkillName"],
      "topic": "Specific topic title",
      "description": "Concrete description",
      "learning_outcomes": ["Outcome 1", "Outcome 2"],
      "expected_level_after_week": "beginner",
      "study_guide": {{
        "what_to_study": [
          "Main concept or API",
          "Secondary concept"
        ],
        "how_to_study": [
          "1. Read/watch something specific",
          "2. Practice a concrete task",
          "3. Apply it to a small task or mini-project"
        ],
        "time_split": {{
          "reading_study": "25%",
          "hands_on_coding": "50%",
          "project_integration": "25%"
        }}
      }},
      "resource_queries": [
        {{
          "title": "Specific technique video",
          "query": "skill technique_keywords level tutorial",
          "type": "youtube"
        }},
        {{
          "title": "Official docs for specific technique",
          "query": "skill technique docs reference",
          "type": "docs"
        }},
        {{
          "title": "Practice exercises for this technique",
          "query": "skill technique exercises practice problems",
          "type": "practice"
        }},
        {{
          "title": "Supplementary guide",
          "query": "skill technique best practices article",
          "type": "article"
        }}
      ]
    }}
  ]
}}
"""


class PlanGenerationService:
    LEVEL_VALUES = {
        "none": 0,
        "beginner": 1,
        "intermediate": 2,
        "advanced": 3,
    }
    LEVEL_NAMES = {0: "none", 1: "beginner", 2: "intermediate", 3: "advanced"}

    def __init__(
        self,
        repository: CareerRepository,
        analysis_service: CareerAnalysisService
    ):
        self.repo = repository
        self.analysis_service = analysis_service
        self.resource_search_service = ResourceSearchService()
        self.llm = create_llm_provider()

    async def generate_plan(
        self,
        cv_id: UUID,
        track_id: int,
        duration_weeks: int,
        available_hours_per_week: Optional[int],
        user_level: Optional[str],
        requested_weeks: Optional[int] = None
    ) -> Dict[str, Any]:
        if duration_weeks <= 0:
            raise ValueError("duration_weeks must be greater than 0")

        track = await self.repo.get_track_by_id(track_id)
        if not track:
            raise ValueError(f"Track not found: {track_id}")

        cached = await self.repo.get_analysis_cache(cv_id=cv_id, track_id=track_id)
        if not cached:
            raise ValueError("No analysis found. Call /analyze then /confirm first.")

        confirmed_learning_targets = cached.get("confirmed_learning_targets", []) or []
        if not confirmed_learning_targets:
            raise ValueError("No confirmed learning targets found. Call /confirm first.")

        available_hours_per_week = (
            available_hours_per_week
            or cached.get("available_hours_per_week")
            or 6
        )

        study_intensity = self._classify_study_intensity(available_hours_per_week)

        effective_user_level = (
            user_level
            or cached.get("level_used")
            or cached.get("detected_level")
            or "beginner"
        )

        # Log if using confirmed user level (from skill confirmations)
        using_confirmed_level = bool(user_level or cached.get("level_used"))
        if using_confirmed_level:
            logger.info(f"Using confirmed user level: {effective_user_level}")
        else:
            logger.info(f"Using detected level as fallback: {effective_user_level}")

        realism = cached.get("realism", {}) or {}
        requested_weeks = requested_weeks or duration_weeks
        skill_gaps = cached.get("skill_gaps", []) or []

        current_level_info = self._calculate_current_track_level(skill_gaps)

        detected_level_from_analysis = self._normalize_level(cached.get("detected_level"))
        if self.LEVEL_VALUES.get(detected_level_from_analysis, 0) > self.LEVEL_VALUES.get(
            current_level_info["current_average_level"], 0
        ):
            current_level_info["current_average_level"] = detected_level_from_analysis

        # ============================================================
        # SELECT learning targets based on zone
        # ============================================================
        zone = realism.get("zone", "suitable")
        fit_analysis = cached.get("fit_analysis", {}) or {}
        can_generate_plan = fit_analysis.get("can_generate_plan", True)

        if can_generate_plan is False:
            planning_mode = "foundation_recovery_plan"
            used_learning_targets = self._filter_targets_foundation_recovery_mode(
                confirmed_learning_targets=confirmed_learning_targets,
                skill_gaps=skill_gaps,
            )
        elif zone in ("below_minimum", "minimum"):
            planning_mode = "minimum_plan"
            used_learning_targets = self._filter_targets_minimum_mode(
                confirmed_learning_targets=confirmed_learning_targets,
                skill_gaps=skill_gaps,
                requested_weeks=requested_weeks,
                available_hours_per_week=available_hours_per_week,
            )
        elif zone == "suitable":
            planning_mode = "suitable_plan"
            used_learning_targets = self._filter_targets_suitable_mode(
                confirmed_learning_targets, skill_gaps
            )
        else:
            planning_mode = "maximum_plan"
            used_learning_targets = self._filter_targets_maximum_mode(
                confirmed_learning_targets, skill_gaps
            )

        deferred_learning_targets: List[Dict[str, Any]] = []

        if not used_learning_targets:
            used_learning_targets = confirmed_learning_targets

        # Normalize learning_mode
        for target in used_learning_targets or []:
            current_level = self._normalize_level(target.get("current_level"))
            target["learning_mode"] = (
                "learn_from_scratch" if current_level == "none" else "level_up"
            )

        # ============================================================
        # PRE-ALLOCATE weeks per skill (KEY IMPROVEMENT)
        # ============================================================
        skill_schedule = self._allocate_weeks_per_skill(
            used_learning_targets=used_learning_targets,
            total_weeks=duration_weeks,
            available_hours_per_week=available_hours_per_week,
        )

        # ============================================================
        # Compute final level info
        # ============================================================
        raw_final_level_info = self._calculate_final_track_level(
            all_skill_gaps=skill_gaps,
            used_learning_targets=used_learning_targets
        )

        minimum_allowed_level = current_level_info["current_average_level"]
        if self.LEVEL_VALUES.get(detected_level_from_analysis, 0) > self.LEVEL_VALUES.get(minimum_allowed_level, 0):
            minimum_allowed_level = detected_level_from_analysis

        final_level_info = self._apply_no_downgrade_guard(
            current_level_info=current_level_info,
            final_level_info=raw_final_level_info,
            detected_level=detected_level_from_analysis,
        )
        final_level_info = self._apply_planning_mode_level_cap(
            final_level_info=final_level_info,
            planning_mode=planning_mode,
            used_learning_targets=used_learning_targets,
            minimum_allowed_level=minimum_allowed_level,
        )

        # ============================================================
        # BUILD PROMPT with skill schedule
        # ============================================================
        prompt = self._build_plan_prompt(
            track_name=track.get("track_name", "Unknown Track"),
            skill_schedule=skill_schedule,
            duration_weeks=duration_weeks,
            available_hours_per_week=available_hours_per_week,
            study_intensity=study_intensity,
            planning_mode=planning_mode,
            current_average_level=current_level_info["current_average_level"],
            final_expected_level=final_level_info["final_expected_level"],
        )

        logger.info(
            "Generating plan... mode=%s targets=%s weeks=%s hours=%s intensity=%s confirmed_user_level=%s",
            planning_mode,
            len(used_learning_targets),
            duration_weeks,
            available_hours_per_week,
            study_intensity,
            effective_user_level if using_confirmed_level else f"{effective_user_level} (fallback)"
        )

        try:
            response = await self.llm.get_response(
                prompt=prompt,
                need_json_output=True,
                expecting_longer_output=True,
                temperature=0.3
            )

            if not response:
                raise ValueError("LLM returned empty response")

            plan_data = response if isinstance(response, dict) else self._safe_parse_json(response)

            weekly_breakdown = plan_data.get("weekly_breakdown", []) or []

            # Post-process
            weekly_breakdown = self._reduce_weekly_repetition(
                weekly_breakdown=weekly_breakdown,
                duration_weeks=duration_weeks,
            )
            weekly_breakdown = self._enforce_weekly_expected_level_guard(
                weekly_breakdown=weekly_breakdown,
                used_learning_targets=used_learning_targets,
                duration_weeks=duration_weeks,
            )

            # Ensure resource mix per week
            for week in weekly_breakdown:
                resource_queries = week.get("resource_queries", []) or []
                week["resource_queries"] = self._ensure_minimum_resource_mix(
                    week=week,
                    resource_queries=resource_queries,
                    available_hours_per_week=available_hours_per_week,
                    duration_weeks=duration_weeks
                )

            plan_data["weekly_breakdown"] = weekly_breakdown
            self._validate_generated_plan(plan_data, duration_weeks, used_learning_targets)

        except Exception as e:
            logger.warning("LLM plan generation failed, using fallback. Reason: %s", e)
            plan_data = self._build_fallback_plan(
                track_name=track.get("track_name", "Unknown Track"),
                skill_schedule=skill_schedule,
                duration_weeks=duration_weeks,
                planning_mode=planning_mode,
                final_expected_level=final_level_info["final_expected_level"]
            )

        # ============================================================
        # FETCH RESOURCES with specific queries per subtopic
        # ============================================================
        weekly_breakdown = plan_data.get("weekly_breakdown", [])

        for week in weekly_breakdown:
            resource_queries = week.pop("resource_queries", []) or []

            # Get skill-specific level context
            level_info = self._infer_week_levels_from_topic(
                week=week,
                used_learning_targets=used_learning_targets
            )

            # Enrich resource queries with subtopic context if generic
            resource_queries = self._enrich_resource_queries(
                resource_queries=resource_queries,
                week_topic=week.get("topic", ""),
                focus_skills=week.get("focus_skills", []),
                current_level=level_info["current_level"],
                target_level=level_info["target_level"],
                week_number=week.get("week_number", 1),
                duration_weeks=duration_weeks,
                available_hours_per_week=available_hours_per_week,
            )

            week["resources"] = await self.resource_search_service.search_resources(
                resource_queries=resource_queries,
                max_per_week=4,
                current_level=level_info["current_level"],
                target_level=level_info["target_level"],
                available_hours_per_week=available_hours_per_week,
                week_number=week.get("week_number"),
                duration_weeks=duration_weeks,
                context_keywords=week.get("focus_skills", []) + [week.get("topic", "")],
            )

        improvement_summary = self._build_improvement_summary(
            track_name=track.get("track_name", "this track"),
            current_level=current_level_info["current_average_level"],
            final_level=final_level_info["final_expected_level"],
            planning_mode=planning_mode
        )

        merged_learning_targets = self._build_merged_learning_targets(
            skill_gaps=skill_gaps,
            confirmed_learning_targets=confirmed_learning_targets
        )

        return {
            "track_id": track_id,
            "track_name": track.get("track_name"),
            "required_level": effective_user_level,
            "duration_weeks": duration_weeks,
            "available_hours_per_week": available_hours_per_week,
            "study_intensity": study_intensity,
            "planning_mode": planning_mode,
            "plan_summary": plan_data.get("plan_summary", ""),
            "current_average_level": current_level_info["current_average_level"],
            "current_track_score": current_level_info["current_track_score"],
            "final_expected_level": final_level_info["final_expected_level"],
            "final_track_score": final_level_info["final_track_score"],
            "final_skill_levels_after_plan": final_level_info["final_skill_levels"],
            "improvement_summary": improvement_summary,
            "weekly_breakdown": weekly_breakdown,
            "learning_targets": confirmed_learning_targets,
            "merged_learning_targets": merged_learning_targets,
            "used_learning_targets": used_learning_targets,
            "deferred_learning_targets": deferred_learning_targets,
            "skill_schedule": skill_schedule,
            "analysis_snapshot": {
                "detected_level": cached.get("detected_level"),
                "required_level": cached.get("required_level"),
                "level_confidence": cached.get("level_confidence"),
                "match_percentage": cached.get("match_percentage"),
                "matching_method": cached.get("matching_method"),
                "fit_analysis": cached.get("fit_analysis"),
                "skill_gaps": skill_gaps,
                "reviewable_skills": cached.get("reviewable_skills", []),
                "detected_skill_levels": cached.get("detected_skill_levels", {}),
                "realism": realism,
                "matched_skills": cached.get("matched_skills", []),
                "missing_skills": cached.get("missing_skills", []),
                "metadata": {
                    "cv_skills_count": len(cached.get("cv_skills", [])),
                    "analysis_quality": cached.get("analysis_quality"),
                    "level_reasoning": cached.get("level_reasoning"),
                }
            }
        }

    # ============================================================
    # WEEK ALLOCATION — Core new method
    # ============================================================

    def _allocate_weeks_per_skill(
        self,
        used_learning_targets: List[Dict[str, Any]],
        total_weeks: int,
        available_hours_per_week: int,
    ) -> List[Dict[str, Any]]:
        """
        Pre-allocate weeks per skill proportionally before LLM call.

        Formula:
        - Base weight = importance_weight × mode_bonus × level_gap_bonus
        - level_up skills get 1.2x (already have base, need depth)
        - Bigger level gap (none->advanced) = more weeks needed
        - Weeks = (skill_weight / total_weight) * total_weeks
        - Minimum 1 week per skill
        """
        if not used_learning_targets or total_weeks <= 0:
            return []

        # Sort: level_up (high value) first, then by importance
        def sort_key(t):
            mode_priority = 2 if t.get("learning_mode") == "level_up" else 1
            importance = int(t.get("importance_weight", 3) or 3)
            return (mode_priority, importance)

        sorted_targets = sorted(used_learning_targets, key=sort_key, reverse=True)

        # Compute weights
        weights = []
        for t in sorted_targets:
            importance = int(t.get("importance_weight", 3) or 3)
            mode_bonus = 1.2 if t.get("learning_mode") == "level_up" else 1.0
            current_val = self.LEVEL_VALUES.get(self._normalize_level(t.get("current_level")), 0)
            target_val = self.LEVEL_VALUES.get(self._normalize_level(t.get("target_level")), 1)
            level_gap = max(1, target_val - current_val)
            weights.append(importance * mode_bonus * level_gap)

        total_weight = sum(weights) or 1.0

        # Proportional allocation
        raw_allocations = [(w / total_weight) * total_weeks for w in weights]
        allocated = [max(1, round(r)) for r in raw_allocations]

        # Fix total to match exactly
        diff = total_weeks - sum(allocated)
        if diff > 0:
            for i in range(diff):
                # Add to highest-weight skills first
                idx = i % len(allocated)
                allocated[idx] += 1
        elif diff < 0:
            for _ in range(abs(diff)):
                max_idx = max(
                    (j for j in range(len(allocated)) if allocated[j] > 1),
                    key=lambda j: allocated[j],
                    default=0
                )
                allocated[max_idx] -= 1

        # Build schedule entries
        schedule = []
        week_cursor = 1

        for i, target in enumerate(sorted_targets):
            skill_name = target.get("skill_name", "")
            current_level = self._normalize_level(target.get("current_level"))
            target_level_val = self._normalize_level(target.get("target_level"))
            learning_mode = target.get("learning_mode", "learn_from_scratch")
            n_weeks = allocated[i]

            subtopic_guide = self._generate_subtopic_guide(
                skill_name=skill_name,
                current_level=current_level,
                target_level=target_level_val,
                learning_mode=learning_mode,
                n_weeks=n_weeks,
                available_hours=available_hours_per_week,
            )

            week_numbers = list(range(week_cursor, week_cursor + n_weeks))
            week_cursor += n_weeks

            schedule.append({
                "skill_name": skill_name,
                "skill_id": target.get("skill_id"),
                "importance_weight": int(target.get("importance_weight", 3) or 3),
                "current_level": current_level,
                "target_level": target_level_val,
                "learning_mode": learning_mode,
                "allocated_weeks": n_weeks,
                "week_numbers": week_numbers,
                "subtopic_guide": subtopic_guide,
                "instruction": self._build_skill_instruction(
                    skill_name, current_level, target_level_val, learning_mode, n_weeks
                ),
            })

        return schedule

    def _generate_subtopic_guide(
        self,
        skill_name: str,
        current_level: str,
        target_level: str,
        learning_mode: str,
        n_weeks: int,
        available_hours: int,
    ) -> List[Dict[str, Any]]:
        """
        Generate week-by-week subtopic progression for a skill.
        Tells the LLM WHAT to cover each week.
        """
        if learning_mode == "learn_from_scratch" or current_level == "none":
            # none → beginner progression stages
            all_stages = [
                "Core concepts, mental model & environment setup",
                "Fundamentals: syntax, basic patterns & guided exercises",
                "Building blocks: key features with hands-on tasks",
                "Small project: integrate concepts end-to-end",
                "Review, debugging patterns & common pitfalls",
                "Real-world mini-project with best practices",
            ]
        elif current_level == "beginner":
            # beginner → intermediate
            all_stages = [
                "Intermediate patterns: beyond the basics",
                "Real-world implementation: professional workflow",
                "Advanced beginner: performance & edge cases",
                "Project: apply intermediate skills in realistic context",
            ]
        else:
            # intermediate → advanced
            all_stages = [
                "Advanced architecture patterns & design decisions",
                "Performance optimization & profiling",
                "Production-grade code: testing, error handling, scalability",
                "Capstone: senior-level real-world scenario",
            ]

        # Take the first n_weeks stages (or repeat last if needed)
        stages = all_stages[:n_weeks]
        while len(stages) < n_weeks:
            stages.append(f"Applied practice & project milestone {len(stages) + 1}")

        return [
            {
                "week_offset": idx + 1,
                "subtopic_focus": stage,
                "expected_level": self._week_level_from_progress(
                    current_level, target_level, idx + 1, n_weeks
                ),
            }
            for idx, stage in enumerate(stages)
        ]

    def _week_level_from_progress(
        self,
        current_level: str,
        target_level: str,
        week_offset: int,
        total_skill_weeks: int,
    ) -> str:
        current_val = self.LEVEL_VALUES.get(current_level, 0)
        target_val = self.LEVEL_VALUES.get(target_level, 1)

        if current_val >= target_val:
            return target_level

        # CRITICAL: For learn_from_scratch (current_level=none), week 1 MUST be beginner
        # This prevents jumping to intermediate/advanced in week 1 of missed skills
        if week_offset == 1:
            if current_val == 0:  # none level
                return "beginner"
            return self.LEVEL_NAMES.get(min(current_val + 1, target_val), "beginner")

        # Progress linearly through remaining weeks
        progress = week_offset / max(total_skill_weeks, 1)
        raw = current_val + (target_val - current_val) * progress
        level_val = min(round(raw + 0.1), target_val)  # slight bias toward progress
        return self.LEVEL_NAMES.get(max(current_val + 1, level_val), target_level)

    def _build_skill_instruction(
        self,
        skill_name: str,
        current_level: str,
        target_level: str,
        learning_mode: str,
        n_weeks: int,
    ) -> str:
        if learning_mode == "learn_from_scratch":
            return (
                f"Teach {skill_name} from zero over {n_weeks} week(s). "
                f"Start with mental model, build up to {target_level} level. "
                f"Each week: different subtopic, no repetition. "
                f"Resource queries must reference the SPECIFIC subtopic/tool, not just '{skill_name}'."
            )
        else:
            return (
                f"User already knows {current_level} level {skill_name}. "
                f"Skip basics. Deepen toward {target_level} over {n_weeks} week(s). "
                f"Focus on {target_level}-level patterns, real-world usage, and practical depth. "
                f"Resource queries must be specific to {target_level} patterns, NOT beginner content."
            )

    def _get_technique_keywords(
        self,
        skill_name: str,
        subtopic: str,
        expected_level: str,
    ) -> str:
        """Extract technique keywords from subtopic for resource queries."""
        subtopic_clean = " ".join(
            (subtopic or "")
            .replace(skill_name, "")
            .replace("&", " and ")
            .replace("/", " ")
            .replace(":", " ")
            .split()
        )
        if subtopic_clean:
            return f"{skill_name} {subtopic_clean} {expected_level}"
        return f"{skill_name} {expected_level} techniques"

    def _build_plan_prompt(
        self,
        track_name: str,
        skill_schedule: List[Dict[str, Any]],
        duration_weeks: int,
        available_hours_per_week: int,
        study_intensity: str,
        planning_mode: str,
        current_average_level: str,
        final_expected_level: str,
    ) -> str:
        """Build structured prompt with pre-computed skill schedule."""
        schedule_for_llm = []

        for entry in skill_schedule:
            skill_name = entry["skill_name"]
            weeks_detail = []

            for guide in entry["subtopic_guide"]:
                week_num = entry["week_numbers"][guide["week_offset"] - 1]
                expected_level = guide["expected_level"]
                technique_keywords = self._get_technique_keywords(
                    skill_name=skill_name,
                    subtopic=guide["subtopic_focus"],
                    expected_level=expected_level,
                )

                weeks_detail.append({
                    "global_week_number": week_num,
                    "subtopic_focus": guide["subtopic_focus"],
                    "expected_level_after_week": expected_level,
                    "technique_keywords": technique_keywords,
                })

            schedule_for_llm.append({
                "skill_name": skill_name,
                "current_level": entry["current_level"],
                "target_level": entry["target_level"],
                "learning_mode": entry["learning_mode"],
                "importance_weight": entry["importance_weight"],
                "allocated_weeks": entry["allocated_weeks"],
                "instruction": entry["instruction"],
                "weeks": weeks_detail,
            })

        return PLAN_GENERATION_PROMPT.format(
            track_name=track_name,
            available_hours_per_week=available_hours_per_week,
            study_intensity=study_intensity,
            duration_weeks=duration_weeks,
            planning_mode=planning_mode,
            current_average_level=current_average_level,
            final_expected_level=final_expected_level,
            skill_schedule_json=json.dumps(schedule_for_llm, ensure_ascii=False, indent=2),
        )

    def _build_study_guide(
        self,
        skill_name: str,
        subtopic: str,
        current_level: str,
        target_level: str,
        available_hours: int,
        week_number: int,
        duration_weeks: int,
    ) -> Dict[str, Any]:
        """
        Dynamically generate study guide with technique-specific steps based on subtopic.
        Extract actual concepts from subtopic to create actionable how_to_study steps.
        """
        # Extract key concepts from subtopic for specific guidance
        subtopic_terms = subtopic.replace("&", " and ").replace("/", " or ").replace(":", " - ")
        is_foundation = week_number <= max(1, duration_weeks // 3)
        is_practice = is_foundation is False and week_number <= max(2, (2 * duration_weeks) // 3)
        is_project = not is_foundation and not is_practice
        
        # Build phase-appropriate but subtopic-specific how_to_study
        if is_foundation:
            phase = "foundation"
            time_split = {
                "reading_study": "35%",
                "hands_on_coding": "45%",
                "project_integration": "20%",
            }
            # DYNAMIC: Extract actual technique concepts
            technique_parts = subtopic_terms.split(" - ")
            main_concept = technique_parts[0].strip() if technique_parts else subtopic_terms
            how_to_study = [
                f"1. Watch/read about the fundamental concept: {main_concept}",
                f"2. Understand the core principles and when to apply {main_concept}",
                f"3. Code 2-3 small examples to practice {main_concept}",
                f"4. Write down key rules and common mistakes with {main_concept}",
            ]
        elif is_practice:
            phase = "practice"
            time_split = {
                "reading_study": "25%",
                "hands_on_coding": "55%",
                "project_integration": "20%",
            }
            # DYNAMIC: Focus on applying the specific techniques
            technique_parts = subtopic_terms.split(" - ")
            technique = technique_parts[0].strip() if technique_parts else subtopic_terms
            how_to_study = [
                f"1. Quickly review {technique} patterns and best practices",
                f"2. Build 3-4 progressively complex scripts using {technique}",
                f"3. Debug and optimize your code for readability and performance",
                f"4. Compare your solution with production examples on GitHub",
            ]
        else:
            phase = "project"
            time_split = {
                "reading_study": "20%",
                "hands_on_coding": "40%",
                "project_integration": "40%",
            }
            # DYNAMIC: Build a project that uses the techniques
            technique_parts = subtopic_terms.split(" - ")
            technique = technique_parts[0].strip() if technique_parts else subtopic_terms
            how_to_study = [
                f"1. Design a mini-project that requires {technique}",
                f"2. Implement it end-to-end using {technique} appropriately",
                f"3. Refactor for clarity and follow best practices",
                f"4. Document your implementation decisions and what you learned",
            ]

        what_to_study = [
            f"Core technique: {subtopic_terms}",
            f"Target: {skill_name} at {target_level} level",
            f"Phase: {phase.title()} — progressing from {current_level} toward {target_level}",
        ]

        return {
            "what_to_study": what_to_study,
            "how_to_study": how_to_study,
            "time_split": time_split,
            "phase": phase,
            "estimated_hours": available_hours,
        }

    def _enrich_resource_queries(
        self,
        resource_queries: List[Dict[str, Any]],
        week_topic: str,
        focus_skills: List[str],
        current_level: str,
        target_level: str,
        week_number: int,
        duration_weeks: int,
        available_hours_per_week: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Post-process resource queries to make them SPECIFIC to the subtopic.
        For YouTube: use technique keywords + level-appropriate search terms.
        Adjust resource type preferences based on available_hours_per_week.
        """
        if not resource_queries:
            return resource_queries

        primary_skill = focus_skills[0] if focus_skills else ""
        stage_ratio = week_number / max(duration_weeks, 1)
        available_hours_per_week = available_hours_per_week or 6

        level_desc = "beginner"
        if current_level in ("intermediate",) or stage_ratio > 0.4:
            level_desc = "intermediate"
        if current_level == "intermediate" and target_level == "advanced":
            level_desc = "advanced"

        # Determine time intensity keywords based on available hours
        if available_hours_per_week <= 5:
            time_intensity = "quick"
            time_keyword = "short tutorial"
        elif available_hours_per_week <= 10:
            time_intensity = "moderate"
            time_keyword = "balanced guide"
        else:
            time_intensity = "deep"
            time_keyword = "comprehensive course"

        enriched = []
        for q in resource_queries:
            query = (q.get("query") or "").strip()
            q_type = (q.get("type") or "article").lower()

            # Detect if query is too generic (just skill name + level word)
            is_generic = (
                query.lower() in [
                    f"{primary_skill.lower()} {level_desc} tutorial",
                    f"{primary_skill.lower()} beginner tutorial",
                    f"{primary_skill.lower()} tutorial",
                    f"{primary_skill.lower()} practice",
                ]
                or len(query.split()) <= 3
            )

            if is_generic and week_topic:
                # Extract topic keywords (remove the skill name if present)
                topic_clean = week_topic.replace(primary_skill, "").strip(" —:-()")
                # Extract 3-5 key technique terms from the topic
                topic_parts = topic_clean.split()
                topic_keywords = " ".join(topic_parts[:5]) if topic_parts else ""

                if q_type == "youtube":
                    # CRITICAL: YouTube queries MUST be specific to the technique
                    # Include: skill + technique + tutorial type + level (if needed) + time hint
                    if level_desc == "beginner":
                        q["query"] = f"{primary_skill} {topic_keywords} beginner tutorial explained step by step"
                    elif level_desc == "advanced":
                        q["query"] = f"{primary_skill} {topic_keywords} advanced patterns production example"
                    else:
                        q["query"] = f"{primary_skill} {topic_keywords} complete tutorial"
                    
                    # Add time hint for limited schedules
                    if available_hours_per_week <= 5:
                        q["query"] += " short video"
                    
                elif q_type in ("docs", "article"):
                    q["query"] = f"{primary_skill} {topic_keywords} documentation reference guide"
                elif q_type in ("practice", "project"):
                    # For heavy schedules, include more complex challenges
                    if available_hours_per_week >= 15:
                        q["query"] = f"{primary_skill} {topic_keywords} advanced exercises challenges"
                    else:
                        q["query"] = f"{primary_skill} {topic_keywords} hands-on exercises"
                elif q_type == "course":
                    # For light schedules, prefer micro-courses; heavy, prefer full courses
                    if available_hours_per_week <= 5:
                        q["query"] = f"{primary_skill} {topic_keywords} micro-course"
                    elif available_hours_per_week >= 15:
                        q["query"] = f"{primary_skill} {topic_keywords} comprehensive course"
                    else:
                        q["query"] = f"{primary_skill} {topic_keywords} structured course"
                
                logger.debug(
                    "Enriched resource query (intensity=%s hours=%d level=%s): %s → %s",
                    time_intensity,
                    available_hours_per_week,
                    level_desc,
                    query[:60],
                    q["query"][:60]
                )

            enriched.append(q)

        return enriched

    # ============================================================
    # FALLBACK PLAN — uses skill_schedule for consistency
    # ============================================================

    def _build_fallback_plan(
        self,
        track_name: str,
        skill_schedule: List[Dict[str, Any]],
        duration_weeks: int,
        planning_mode: str = "full_plan",
        final_expected_level: str = "beginner"
    ) -> Dict[str, Any]:
        """
        Fallback plan using the pre-computed skill schedule.
        More coherent than the old random rotation approach.
        """
        if not skill_schedule:
            raise ValueError("Cannot build fallback plan without skill_schedule")

        weeks = []

        for entry in skill_schedule:
            skill_name = entry["skill_name"]
            current_level = entry["current_level"]
            target_level = entry["target_level"]
            learning_mode = entry["learning_mode"]

            for guide in entry["subtopic_guide"]:
                week_number = entry["week_numbers"][guide["week_offset"] - 1]
                subtopic = guide["subtopic_focus"]
                expected_level = guide["expected_level"]

                if learning_mode == "level_up":
                    topic = f"{skill_name}: {subtopic} ({current_level} → {target_level})"
                    description = (
                        f"This week deepens {skill_name} from {current_level} toward {target_level}. "
                        f"Focus: {subtopic}. "
                        f"Work through targeted exercises and real-world scenarios at {target_level} level."
                    )
                    tutorial_query = f"{skill_name} {subtopic} {target_level} tutorial"
                else:
                    topic = f"{skill_name}: {subtopic}"
                    description = (
                        f"This week introduces {skill_name} starting from {subtopic}. "
                        f"Follow guided examples, complete hands-on exercises, "
                        f"and build toward {expected_level} level understanding."
                    )
                    tutorial_query = f"{skill_name} {subtopic} beginner tutorial"

                # Subtopic-specific resource queries
                subtopic_kw = subtopic.replace("&", "and").replace("/", " ").lower()
                subtopic_kw_short = " ".join(subtopic_kw.split()[:4])

                # Generate study guide for this week
                study_guide = self._build_study_guide(
                    skill_name=skill_name,
                    subtopic=subtopic,
                    current_level=current_level,
                    target_level=target_level,
                    available_hours=6,  # Default fallback hours
                    week_number=week_number,
                    duration_weeks=duration_weeks,
                )

                weeks.append({
                    "week_number": week_number,
                    "focus_skills": [skill_name],
                    "topic": topic,
                    "description": description,
                    "learning_outcomes": [
                        f"Understand and apply {skill_name}: {subtopic}",
                        f"Progress {skill_name} toward {expected_level} level through practice"
                    ],
                    "expected_level_after_week": expected_level,
                    "study_guide": study_guide,
                    "resource_queries": [
                        {
                            "title": f"{skill_name} {subtopic_kw_short} — Video Tutorial",
                            "query": tutorial_query,
                            "type": "youtube"
                        },
                        {
                            "title": f"{skill_name} {subtopic_kw_short} — Documentation",
                            "query": f"{skill_name} {subtopic_kw_short} official documentation",
                            "type": "docs"
                        },
                        {
                            "title": f"{skill_name} {subtopic_kw_short} — Practice",
                            "query": f"{skill_name} {subtopic_kw_short} exercises practice",
                            "type": "practice"
                        },
                        {
                            "title": f"{skill_name} {subtopic_kw_short} — Course/Guide",
                            "query": f"{skill_name} {subtopic_kw_short} complete guide course",
                            "type": "course"
                        }
                    ]
                })

        # Sort by week number
        weeks.sort(key=lambda w: w["week_number"])

        plan_summary = (
            f"This {duration_weeks}-week plan for the {track_name} track builds skills "
            f"progressively from your current level, allocating focused weeks per skill "
            f"based on importance and learning gap."
        )

        return {
            "plan_summary": plan_summary,
            "improvement_summary": (
                f"After completing this plan, you will have strengthened your skills "
                f"toward {final_expected_level} level across key {track_name} competencies."
            ),
            "weekly_breakdown": weeks,
        }

    # ============================================================
    # UNCHANGED METHODS (kept from original)
    # ============================================================

    def _filter_targets_minimum_mode(
        self,
        confirmed_learning_targets: List[Dict[str, Any]],
        skill_gaps: List[Dict[str, Any]],
        requested_weeks: int,
        available_hours_per_week: int,
    ) -> List[Dict[str, Any]]:
        filtered = []
        seen_ids = set()
        for target in confirmed_learning_targets or []:
            skill_id = target.get("skill_id")
            if skill_id in seen_ids:
                continue
            filtered.append({
                **target,
                "target_level": self._normalize_level(target.get("target_level")),
                "learning_mode": target.get("learning_mode", "learn_from_scratch"),
            })
            seen_ids.add(skill_id)
        return filtered

    def _filter_targets_suitable_mode(
        self,
        confirmed_learning_targets: List[Dict[str, Any]],
        skill_gaps: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        filtered = []
        seen_ids = set()
        for target in confirmed_learning_targets:
            skill_id = target.get("skill_id")
            if skill_id in seen_ids:
                continue
            is_core = self._is_skill_core(skill_id, skill_gaps)
            current_level = self._normalize_level(target.get("current_level"))
            target_level = "intermediate" if (is_core and current_level in ("none", "beginner")) else "beginner"
            learning_mode = "learn_from_scratch" if current_level == "none" else "level_up"
            filtered.append({
                **target,
                "current_level": current_level,
                "target_level": target_level,
                "learning_mode": learning_mode,
            })
            seen_ids.add(skill_id)
        for gap in skill_gaps:
            if gap.get("status") != "has":
                continue
            skill_id = gap.get("skill_id")
            if skill_id in seen_ids:
                continue
            is_core = gap.get("is_core", True)
            current_level = self._normalize_level(gap.get("current_level"))
            if is_core and self.LEVEL_VALUES.get(current_level, 0) < self.LEVEL_VALUES["intermediate"]:
                filtered.append({
                    "skill_id": skill_id,
                    "skill_name": gap.get("skill_name"),
                    "current_level": current_level,
                    "target_level": "intermediate",
                    "learning_mode": "level_up",
                    "required_level": self._normalize_level(gap.get("required_level")),
                    "required_weeks": gap.get("required_weeks", 4),
                    "importance_weight": int(gap.get("importance_weight", 3) or 3),
                })
                seen_ids.add(skill_id)
        return filtered

    def _filter_targets_maximum_mode(
        self,
        confirmed_learning_targets: List[Dict[str, Any]],
        skill_gaps: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        filtered = []
        seen_ids = set()
        for target in confirmed_learning_targets:
            skill_id = target.get("skill_id")
            if skill_id in seen_ids:
                continue
            current_level = self._normalize_level(target.get("current_level"))
            filtered.append({
                **target,
                "current_level": current_level,
                "target_level": "advanced",
                "learning_mode": "learn_from_scratch" if current_level == "none" else "level_up",
            })
            seen_ids.add(skill_id)
        for gap in skill_gaps:
            if gap.get("status") != "has":
                continue
            skill_id = gap.get("skill_id")
            if skill_id in seen_ids:
                continue
            current_level = self._normalize_level(gap.get("current_level"))
            if self.LEVEL_VALUES.get(current_level, 0) >= self.LEVEL_VALUES["advanced"]:
                continue
            filtered.append({
                "skill_id": skill_id,
                "skill_name": gap.get("skill_name"),
                "current_level": current_level,
                "target_level": "advanced",
                "learning_mode": "level_up",
                "required_level": self._normalize_level(gap.get("required_level")),
                "required_weeks": gap.get("required_weeks", 4),
                "importance_weight": int(gap.get("importance_weight", 3) or 3),
            })
            seen_ids.add(skill_id)
        return filtered

    def _filter_targets_foundation_recovery_mode(
        self,
        confirmed_learning_targets: List[Dict[str, Any]],
        skill_gaps: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        filtered = []
        seen_ids = set()
        for target in confirmed_learning_targets or []:
            skill_id = target.get("skill_id")
            if skill_id in seen_ids:
                continue
            current_level = self._normalize_level(target.get("current_level"))
            filtered.append({
                **target,
                "current_level": current_level,
                "target_level": "beginner" if current_level == "none" else "intermediate",
                "learning_mode": "learn_from_scratch" if current_level == "none" else "level_up",
            })
            seen_ids.add(skill_id)
        for gap in skill_gaps or []:
            if gap.get("status") != "missing" or not gap.get("is_core", True):
                continue
            skill_id = gap.get("skill_id")
            if skill_id in seen_ids:
                continue
            filtered.append({
                "skill_id": skill_id,
                "skill_name": gap.get("skill_name"),
                "current_level": "none",
                "target_level": "beginner",
                "learning_mode": "learn_from_scratch",
                "required_level": self._normalize_level(gap.get("required_level")),
                "required_weeks": int(gap.get("required_weeks", 4) or 4),
                "importance_weight": int(gap.get("importance_weight", 3) or 3),
            })
            seen_ids.add(skill_id)
        return filtered

    def _is_skill_core(self, skill_id: int, skill_gaps: List[Dict[str, Any]]) -> bool:
        for gap in skill_gaps:
            if gap.get("skill_id") == skill_id:
                return gap.get("is_core", True)
        return True

    def _build_merged_learning_targets(
        self,
        skill_gaps: List[Dict[str, Any]],
        confirmed_learning_targets: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        merged = []
        seen_ids = set()
        for item in confirmed_learning_targets or []:
            skill_id = item.get("skill_id")
            if skill_id is None or skill_id in seen_ids:
                continue
            merged.append({
                "skill_id": skill_id,
                "skill_name": item.get("skill_name"),
                "current_level": self._normalize_level(item.get("current_level")),
                "target_level": self._normalize_level(item.get("target_level")),
                "learning_mode": item.get("learning_mode", "learn_from_scratch"),
                "required_level": self._normalize_level(item.get("required_level")),
                "required_weeks": int(item.get("required_weeks", 4) or 4),
                "importance_weight": int(item.get("importance_weight", 3) or 3),
            })
            seen_ids.add(skill_id)
        for gap in skill_gaps or []:
            if gap.get("status") != "has":
                continue
            skill_id = gap.get("skill_id")
            if skill_id is None or skill_id in seen_ids:
                continue
            current_level = self._normalize_level(gap.get("current_level"))
            if current_level not in ("beginner", "intermediate"):
                continue
            target_level = "intermediate" if current_level == "beginner" else "advanced"
            merged.append({
                "skill_id": skill_id,
                "skill_name": gap.get("skill_name"),
                "current_level": current_level,
                "target_level": target_level,
                "learning_mode": "level_up",
                "required_level": self._normalize_level(gap.get("required_level")),
                "required_weeks": 6 if current_level == "beginner" else 8,
                "importance_weight": int(gap.get("importance_weight", 3) or 3),
            })
            seen_ids.add(skill_id)
        return merged

    def _classify_study_intensity(self, hours: int) -> str:
        if hours <= 5:
            return "light"
        elif hours <= 10:
            return "moderate"
        return "intensive"

    def _normalize_level(self, level: Optional[str]) -> str:
        value = (level or "none").strip().lower()
        return value if value in self.LEVEL_VALUES else "none"

    def _get_next_level(self, current_level: str) -> str:
        current_level = self._normalize_level(current_level)
        mapping = {"none": "beginner", "beginner": "intermediate", "intermediate": "advanced"}
        return mapping.get(current_level, "advanced")

    def _score_to_level(self, score: float, included_skills_count: int) -> str:
        if included_skills_count < 3 and score < 1.5:
            return "beginner"
        if score >= 2.5:
            return "advanced"
        if score >= 1.5:
            return "intermediate"
        return "beginner"

    def _calculate_current_track_level(self, all_skill_gaps: List[Dict[str, Any]]) -> Dict[str, Any]:
        current_skill_levels = {}
        total_weight = 0
        weighted_sum = 0.0
        for gap in all_skill_gaps or []:
            current_level = self._normalize_level(gap.get("current_level"))
            if current_level == "none":
                continue
            skill_name = gap.get("skill_name", "Unknown")
            importance = int(gap.get("importance_weight", 3) or 3)
            level_value = self.LEVEL_VALUES.get(current_level, 0)
            current_skill_levels[skill_name] = current_level
            total_weight += importance
            weighted_sum += level_value * importance
        if total_weight == 0:
            return {"current_average_level": "beginner", "current_track_score": 1.0, "current_skill_levels": {}}
        avg_score = weighted_sum / total_weight
        return {
            "current_average_level": self._score_to_level(avg_score, len(current_skill_levels)),
            "current_track_score": round(avg_score, 2),
            "current_skill_levels": current_skill_levels,
        }

    def _calculate_final_track_level(
        self,
        all_skill_gaps: List[Dict[str, Any]],
        used_learning_targets: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        final_skill_levels: Dict[int, str] = {}
        skill_meta: Dict[int, Dict[str, Any]] = {}
        for gap in all_skill_gaps or []:
            skill_id = gap.get("skill_id")
            if skill_id is None:
                continue
            current_level = self._normalize_level(gap.get("current_level"))
            if current_level == "none":
                continue
            final_skill_levels[skill_id] = current_level
            skill_meta[skill_id] = {
                "skill_name": gap.get("skill_name"),
                "importance_weight": int(gap.get("importance_weight", 3) or 3),
            }
        for target in used_learning_targets or []:
            skill_id = target.get("skill_id")
            if skill_id is None:
                continue
            final_skill_levels[skill_id] = self._normalize_level(target.get("target_level"))
            if skill_id not in skill_meta:
                skill_meta[skill_id] = {
                    "skill_name": target.get("skill_name"),
                    "importance_weight": int(target.get("importance_weight", 3) or 3),
                }
        total_weight = 0
        weighted_sum = 0.0
        for skill_id, level_name in final_skill_levels.items():
            importance = int(skill_meta.get(skill_id, {}).get("importance_weight", 3) or 3)
            level_value = self.LEVEL_VALUES.get(level_name, 0)
            total_weight += importance
            weighted_sum += level_value * importance
        if total_weight == 0:
            return {"final_expected_level": "beginner", "final_track_score": 1.0, "final_skill_levels": {}}
        avg_score = weighted_sum / total_weight
        human_readable = {
            skill_meta.get(sid, {}).get("skill_name", str(sid)): lvl
            for sid, lvl in final_skill_levels.items()
        }
        return {
            "final_expected_level": self._score_to_level(avg_score, len(final_skill_levels)),
            "final_track_score": round(avg_score, 2),
            "final_skill_levels": human_readable,
        }

    def _apply_no_downgrade_guard(
        self,
        current_level_info: Dict[str, Any],
        final_level_info: Dict[str, Any],
        detected_level: Optional[str] = None,
    ) -> Dict[str, Any]:
        current_score = float(current_level_info.get("current_track_score", 1.0) or 1.0)
        final_score = float(final_level_info.get("final_track_score", 1.0) or 1.0)
        current_level = self._normalize_level(current_level_info.get("current_average_level"))
        final_level = self._normalize_level(final_level_info.get("final_expected_level"))
        detected_level = self._normalize_level(detected_level)
        minimum_allowed = current_level
        if self.LEVEL_VALUES.get(detected_level, 0) > self.LEVEL_VALUES.get(minimum_allowed, 0):
            minimum_allowed = detected_level
        if self.LEVEL_VALUES.get(final_level, 0) < self.LEVEL_VALUES.get(minimum_allowed, 0) or final_score < current_score:
            final_level_info["final_expected_level"] = minimum_allowed
            final_level_info["final_track_score"] = round(max(final_score, current_score), 2)
        return final_level_info

    def _apply_planning_mode_level_cap(
        self,
        final_level_info: Dict[str, Any],
        planning_mode: str,
        used_learning_targets: List[Dict[str, Any]],
        minimum_allowed_level: Optional[str] = None,
    ) -> Dict[str, Any]:
        mode_max = "advanced" if planning_mode == "maximum_plan" else "intermediate"
        minimum_allowed = self._normalize_level(minimum_allowed_level or "beginner")
        current_final = self._normalize_level(final_level_info.get("final_expected_level"))
        if self.LEVEL_VALUES.get(current_final, 0) > self.LEVEL_VALUES.get(mode_max, 0):
            current_final = mode_max
        if self.LEVEL_VALUES.get(current_final, 0) < self.LEVEL_VALUES.get(minimum_allowed, 0):
            current_final = minimum_allowed
        final_level_info["final_expected_level"] = current_final
        capped = {}
        for skill_name, level in (final_level_info.get("final_skill_levels") or {}).items():
            normalized = self._normalize_level(level)
            if self.LEVEL_VALUES.get(normalized, 0) > self.LEVEL_VALUES.get(mode_max, 0):
                normalized = mode_max
            capped[skill_name] = normalized
        final_level_info["final_skill_levels"] = capped
        return final_level_info

    def _build_improvement_summary(self, track_name, current_level, final_level, planning_mode) -> str:
        if final_level == current_level:
            msg = (
                f"After finishing this plan, your overall expected level in {track_name} "
                f"remains {final_level}, with stronger practical depth across the track."
            )
        else:
            msg = (
                f"After finishing this plan, your overall expected level in {track_name} "
                f"improves from {current_level} to {final_level}."
            )
        if planning_mode == "adaptive_compressed_plan":
            msg += " This compressed plan focuses on the highest-priority skills first."
        return msg

    def _ensure_minimum_resource_mix(
        self,
        week: Dict[str, Any],
        resource_queries: List[Dict[str, Any]],
        available_hours_per_week: int,
        duration_weeks: int
    ) -> List[Dict[str, Any]]:
        queries = list(resource_queries or [])
        focus_skills = week.get("focus_skills", []) or []
        primary_skill = focus_skills[0] if focus_skills else (week.get("topic") or "skill")
        week_topic = week.get("topic", primary_skill)
        week_number = int(week.get("week_number", 1) or 1)
        stage_ratio = week_number / max(duration_weeks, 1)

        # Extract meaningful keywords from topic
        topic_keywords = " ".join(week_topic.replace(primary_skill, "").strip(" —:-").split()[:4])
        if not topic_keywords:
            topic_keywords = "fundamentals" if stage_ratio <= 0.33 else "advanced patterns"

        difficulty = "beginner" if stage_ratio <= 0.33 else ("intermediate" if stage_ratio <= 0.75 else "advanced")

        has_youtube = any((q.get("type") or "").lower() == "youtube" for q in queries)
        has_non_youtube = any((q.get("type") or "").lower() != "youtube" for q in queries)
        has_practice = any((q.get("type") or "").lower() in ("practice", "project") for q in queries)

        if not has_youtube:
            queries.append({
                "title": f"{primary_skill} {topic_keywords} video tutorial",
                "query": f"{primary_skill} {topic_keywords} {difficulty} tutorial",
                "type": "youtube"
            })
        if not has_non_youtube:
            queries.append({
                "title": f"{primary_skill} {topic_keywords} documentation",
                "query": f"{primary_skill} {topic_keywords} official documentation guide",
                "type": "docs"
            })
        if not has_practice:
            queries.append({
                "title": f"{primary_skill} {topic_keywords} practice",
                "query": f"{primary_skill} {topic_keywords} exercises hands-on",
                "type": "practice"
            })
        while len(queries) < 4:
            queries.append({
                "title": f"{primary_skill} {topic_keywords} article",
                "query": f"{primary_skill} {topic_keywords} best practices {difficulty}",
                "type": "article"
            })

        return queries[:4]

    def _infer_week_levels_from_topic(
        self,
        week: Dict[str, Any],
        used_learning_targets: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        focus_skills = week.get("focus_skills", []) or []
        primary_skill = focus_skills[0].strip().lower() if focus_skills else ""
        for target in used_learning_targets or []:
            if (target.get("skill_name") or "").strip().lower() == primary_skill:
                return {
                    "current_level": self._normalize_level(target.get("current_level")),
                    "target_level": self._normalize_level(target.get("target_level")),
                }
        return {"current_level": "beginner", "target_level": "intermediate"}

    def _reduce_weekly_repetition(
        self,
        weekly_breakdown: List[Dict[str, Any]],
        duration_weeks: int,
    ) -> List[Dict[str, Any]]:
        if not weekly_breakdown:
            return weekly_breakdown
        seen: set = set()
        for week in weekly_breakdown:
            focus_skills = week.get("focus_skills", []) or []
            main_skill = focus_skills[0] if focus_skills else "Core Skill"
            topic = (week.get("topic") or "").strip()
            key = (tuple(sorted(s.lower() for s in focus_skills)), topic.lower())
            if key in seen:
                week_number = int(week.get("week_number", 1) or 1)
                if week_number <= max(1, duration_weeks // 3):
                    week["topic"] = f"Concept Deepening: {main_skill}"
                elif week_number <= max(2, (duration_weeks * 2) // 3):
                    week["topic"] = f"Practical Workflow: {main_skill}"
                else:
                    week["topic"] = f"Mini Project Milestone: {main_skill}"
                desc = (week.get("description") or "").strip()
                if desc:
                    week["description"] = f"{desc} This week builds on prior learning with new focus."
            seen.add((
                tuple(sorted(s.lower() for s in focus_skills)),
                (week.get("topic") or "").strip().lower()
            ))
        return weekly_breakdown

    def _enforce_weekly_expected_level_guard(
        self,
        weekly_breakdown: List[Dict[str, Any]],
        used_learning_targets: List[Dict[str, Any]],
        duration_weeks: int,
    ) -> List[Dict[str, Any]]:
        skill_map = {}
        for target in used_learning_targets or []:
            skill_name = (target.get("skill_name") or "").strip().lower()
            skill_map[skill_name] = {
                "current_level": self._normalize_level(target.get("current_level")),
                "target_level": self._normalize_level(target.get("target_level")),
            }
        for week in weekly_breakdown or []:
            focus_skills = week.get("focus_skills", []) or []
            if not focus_skills:
                continue
            primary_skill = (focus_skills[0] or "").strip().lower()
            target_info = skill_map.get(primary_skill)
            if not target_info:
                continue
            current_level = target_info["current_level"]
            target_level = target_info["target_level"]
            declared_level = self._normalize_level(week.get("expected_level_after_week"))
            week_number = int(week.get("week_number", 1) or 1)
            if current_level == "none":
                allowed = "beginner" if week_number <= max(2, duration_weeks // 3) else (
                    "intermediate" if week_number <= max(4, (duration_weeks * 2) // 3) else "advanced"
                )
            elif current_level == "beginner":
                allowed = "intermediate" if week_number <= 2 else "advanced"
            else:
                allowed = "advanced"
            if self.LEVEL_VALUES.get(allowed, 0) > self.LEVEL_VALUES.get(target_level, 0):
                allowed = target_level
            if self.LEVEL_VALUES.get(declared_level, 0) > self.LEVEL_VALUES.get(allowed, 0):
                week["expected_level_after_week"] = allowed
        return weekly_breakdown

    def _safe_parse_json(self, raw_response: Any) -> Dict[str, Any]:
        """Safely parse JSON from LLM response, extracting valid JSON even if malformed."""
        if isinstance(raw_response, dict):
            return raw_response
        
        if not isinstance(raw_response, str):
            raw_response = str(raw_response)
        
        # Try direct parse first
        try:
            return json.loads(raw_response)
        except json.JSONDecodeError:
            pass
        
        # Try to extract JSON from markdown code blocks
        json_match = re.search(r'```(?:json)?\s*(.+?)\s*```', raw_response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # Try to find and extract the main JSON object
        try:
            # Find first { and last }
            start = raw_response.find('{')
            end = raw_response.rfind('}')
            if start != -1 and end != -1 and end > start:
                json_str = raw_response[start:end+1]
                return json.loads(json_str)
        except json.JSONDecodeError:
            pass
        
        # Try to fix common JSON errors
        try:
            # Remove trailing commas before ] or }
            fixed = re.sub(r',\s*([\]\}])', r'\1', raw_response)
            # Remove quotes around keys if needed
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        
        # Handle truncated JSON - close missing brackets
        try:
            # Count opening and closing brackets
            open_braces = raw_response.count('{')
            close_braces = raw_response.count('}')
            open_brackets = raw_response.count('[')
            close_brackets = raw_response.count(']')
            
            # Add missing closing brackets
            if open_braces > close_braces:
                fixed = raw_response + '}' * (open_braces - close_braces)
            elif open_brackets > close_brackets:
                fixed = raw_response + ']' * (open_brackets - close_brackets)
            else:
                fixed = raw_response
            
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        
        raise ValueError(f"Failed to extract valid JSON from LLM response. Length: {len(raw_response)}, First 300 chars: {raw_response[:300]}")

    def _validate_generated_plan(
        self,
        plan_data: Dict[str, Any],
        duration_weeks: int,
        used_learning_targets: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        if not isinstance(plan_data, dict):
            raise ValueError("Generated plan must be a JSON object")
        weekly_breakdown = plan_data.get("weekly_breakdown")
        if not isinstance(weekly_breakdown, list) or not weekly_breakdown:
            raise ValueError("Generated plan is missing weekly_breakdown")
        if len(weekly_breakdown) != duration_weeks:
            raise ValueError(
                f"Generated plan weeks mismatch. Expected {duration_weeks}, got {len(weekly_breakdown)}"
            )
        used_learning_targets = used_learning_targets or []
        for i, week in enumerate(weekly_breakdown, start=1):
            if not isinstance(week, dict):
                raise ValueError(f"Week {i} must be an object")
            if week.get("week_number") != i:
                raise ValueError(f"Invalid week numbering at week {i}")
            for field in ["focus_skills", "topic", "description", "learning_outcomes", "expected_level_after_week"]:
                if field not in week:
                    raise ValueError(f"Week {i} missing field: {field}")
            resource_queries = week.get("resource_queries", []) or []
            if resource_queries:
                types = [q.get("type", "").lower() for q in resource_queries if q.get("type")]
                if types and len(set(types)) == 1:
                    raise ValueError(f"Week {i}: ALL resources are type '{types[0]}'. Must have MIXED types.")
                if all(t == "youtube" for t in types):
                    raise ValueError(f"Week {i}: NO non-YouTube resources.")
            focus_skills = week.get("focus_skills", []) or []
            expected_level = self._normalize_level(week.get("expected_level_after_week"))
            for skill_name in focus_skills:
                matching_target = next(
                    (t for t in used_learning_targets
                     if (t.get("skill_name") or "").strip().lower() == skill_name.strip().lower()),
                    None
                )
                if not matching_target:
                    continue
                current = self._normalize_level(matching_target.get("current_level"))
                target_level = self._normalize_level(matching_target.get("target_level"))
                if i == 1 and current == "none" and expected_level == "advanced":
                    raise ValueError(f"Week {i} ({skill_name}): Cannot jump none→advanced in week 1.")
                if self.LEVEL_VALUES.get(expected_level, 0) > self.LEVEL_VALUES.get(target_level, 0):
                    raise ValueError(
                        f"Week {i} ({skill_name}): expected_level='{expected_level}' exceeds target='{target_level}'."
                    )
                if i == 1 and current == "none" and expected_level not in ("beginner", "none"):
                    raise ValueError(f"Week {i} ({skill_name}): week 1 with none can only reach beginner.")
        logger.info("VALIDATION PASSED: %s weeks", len(weekly_breakdown))