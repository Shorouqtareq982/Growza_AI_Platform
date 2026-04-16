"""
Plan Regeneration Service - IMPROVED VERSION
Key improvements:
1. Better regeneration prompt that references skill schedule
2. Looser validation (trust level guard instead of hard-rejecting)
3. Feedback-specific resource queries
4. Consistent with improved generation service
"""

import json
import logging
from typing import Dict, Any, List, Optional, Set

from shared.providers.llm_models.llm_provider import create_llm_provider
from features.career_builder.services.resource_search_service import ResourceSearchService
from features.career_builder.services.plan_feedback_mapper import PlanFeedbackMapper

logger = logging.getLogger(__name__)


PLAN_REGENERATION_PROMPT = """
You are a senior software engineering mentor revising a personalized learning plan.

User feedback has been provided and you MUST apply it meaningfully to create a significantly improved plan.
This is NOT a cosmetic change — the plan should be notably different where feedback applies.

==============================================================
FEEDBACK TO APPLY (CRITICAL):
==============================================================
{feedback_instructions}

==============================================================
REGENERATION MODE: {regeneration_mode}
==============================================================
Modes:
- "full": Regenerate entire plan with all feedback applied
- "partial": Focus feedback on first 50% of weeks
- "focused": Apply feedback only to specific problematic weeks

==============================================================
MANDATORY CONSTRAINTS:
==============================================================
1. Return VALID JSON only (no markdown, no extra text)
2. Keep EXACT same number of weeks: {duration_weeks} weeks
3. Keep EXACT same skills (only from used_learning_targets)
4. Keep week numbering sequential from 1 to {duration_weeks}
5. NO URLs in anything — use resource_queries only
6. EVERY week must have exactly 4 resource_queries
7. Apply feedback VISIBLY and MEANINGFULLY:
   - Change topics, descriptions, and structure where feedback applies
   - Do NOT make cosmetic/superficial changes
   - Do NOT ignore feedback or apply it weakly

==============================================================
FEEDBACK APPLICATION CHECKLIST:
==============================================================
BEFORE RETURNING, verify:
□ Are topics/descriptions noticeably different from original where feedback applies?
□ Have you changed resource types or emphasis based on feedback?
□ For "more practical": Are there more project/hands-on elements now?
□ For "more advanced": Are topics deeper and more sophisticated?
□ For "less repetition": Does each week cover completely new material?
□ For "focus selected": Are only selected skills in the plan?
□ For "faster progress": Do you reach applications faster?
□ For "simpler basics": Are prerequisites explicitly covered first?
□ For "more projects": Do you have capstone projects?
□ For "more theory": Are foundations explained before application?
□ For "better structure": Do prerequisites come before dependent skills?

==============================================================
RESOURCE QUERY RULES (STRICT):
==============================================================
EVERY week MUST have 4 DISTINCT resource queries:
  1. ONE "youtube" query — specific to THIS week's subtopic (NOT generic)
  2. ONE "docs" OR "article" — deep reference material for subtopic
  3. ONE "practice" OR "project" — hands-on exercises for subtopic
  4. ONE additional type (course, article, practice, project) — supplementary

CRITICAL EXAMPLES:
BAD query: "React tutorial", "Redux beginners guide"
GOOD query: "React useEffect cleanup patterns memory leaks tutorial"
GOOD query: "Redux thunk async middleware tutorial"

Resource queries MUST reflect the feedback emphasis:
- If "more practical": queries use "project", "hands-on", "implementation"
- If "more advanced": queries mention "advanced", "patterns", "optimization"
- If "more theory": queries mention "concepts", "architecture", "design"

==============================================================
TOPIC/DESCRIPTION TRANSFORMATION EXAMPLES:
==============================================================

Original Topic: "Getting Started with React"
If more_practical: "Building Your First React Component: DOM Manipulation Project"
If more_advanced: "React Fiber Architecture & Rendering Optimization"
If more_theory: "React Fundamentals: Virtual DOM Concepts & Reconciliation"

Original Description: "Learn React basics"
If more_practical: "Build an interactive component from scratch. Start with JSX syntax, then create state-based UI changes."
If more_theory: "Understand Virtual DOM reconciliation. Study React's diffing algorithm and lifecycle methods."

==============================================================
ORIGINAL PLAN REFERENCE:
==============================================================
{previous_plan_json}

==============================================================
OUTPUT MUST BE VALID JSON:
==============================================================
{{
  "plan_summary": "Updated summary reflecting feedback changes (2-3 sentences)",
  "improvement_summary": "Specific description of how feedback was applied (1-2 sentences)",
  "weekly_breakdown": [
    {{
      "week_number": 1,
      "focus_skills": ["SkillName"],
      "topic": "Significantly updated topic title reflecting feedback",
      "description": "Concrete description (2-3 sentences): what user DOES this week, specific tools, concepts, exercises, outputs.",
      "learning_outcomes": ["Specific, measurable outcome 1", "Specific outcome 2"],
      "expected_level_after_week": "beginner|intermediate|advanced",
      "resource_queries": [
        {{"title": "Specific video title", "query": "skill specific_subtopic tutorial or technique", "type": "youtube"}},
        {{"title": "Specific docs title", "query": "skill specific_subtopic documentation API reference", "type": "docs"}},
        {{"title": "Specific practice title", "query": "skill specific_subtopic exercises hands-on challenges", "type": "practice"}},
        {{"title": "Specific supplementary title", "query": "skill specific_subtopic project case study guide", "type": "project"}}
      ]
    }}
  ]
}}

REMEMBER: The plan should feel MEANINGFULLY DIFFERENT from the original when feedback is applied.
If regenerated plan looks similar to original, you have NOT applied feedback properly.
"""


class PlanRegenerationService:
    LEVEL_VALUES = {
        "none": 0,
        "beginner": 1,
        "intermediate": 2,
        "advanced": 3,
    }

    def __init__(self):
        self.llm = create_llm_provider()
        self.resource_search_service = ResourceSearchService()

    async def regenerate_plan(
        self,
        previous_plan: Dict[str, Any],
        feedback_intents: List[str],
        regeneration_mode: str = "full"
    ) -> Dict[str, Any]:
        if not previous_plan:
            raise ValueError("previous_plan is required")

        duration_weeks = previous_plan.get("duration_weeks", 0)
        if not isinstance(duration_weeks, int) or duration_weeks <= 0:
            raise ValueError("previous_plan must contain a valid duration_weeks")

        if not feedback_intents or not isinstance(feedback_intents, list):
            raise ValueError("feedback_intents must be a non-empty list")

        feedback_intents_str = [self._normalize_intent(intent) for intent in feedback_intents]

        try:
            validated_intents = PlanFeedbackMapper.validate_intents(feedback_intents_str)
        except ValueError as e:
            raise ValueError(f"Invalid feedback intents: {e}")

        feedback_instruction = PlanFeedbackMapper.map_intents_to_instruction(validated_intents)

        # Build compact previous plan context (avoid token bloat)
        compact_context = self._build_compact_context(previous_plan)

        prompt = PLAN_REGENERATION_PROMPT.format(
            feedback_instructions=feedback_instruction,
            regeneration_mode=regeneration_mode,
            duration_weeks=duration_weeks,
            previous_plan_json=json.dumps(compact_context, ensure_ascii=False, indent=2)
        )

        logger.info(
            "Regenerating plan with feedback... mode=%s intents=%s (%s) duration=%s weeks",
            regeneration_mode,
            feedback_intents_str,
            ", ".join([f"{intent}" for intent in feedback_intents_str]),
            duration_weeks,
        )

        try:
            response = await self.llm.get_response(
                prompt=prompt,
                need_json_output=True,
                temperature=0.35
            )

            if not response:
                raise ValueError("LLM returned empty response")

            plan_data = response if isinstance(response, dict) else self._safe_parse_json(response)
            weekly_breakdown = plan_data.get("weekly_breakdown", [])

            used_learning_targets = previous_plan.get("used_learning_targets", []) or []
            available_hours_per_week = previous_plan.get("available_hours_per_week", 6)
            fallback_target_level = previous_plan.get("final_expected_level", "intermediate")

            # Soft validation — fix issues instead of rejecting
            weekly_breakdown = self._soft_validate_and_fix(
                weekly_breakdown=weekly_breakdown,
                duration_weeks=duration_weeks,
                used_learning_targets=used_learning_targets,
            )

            weekly_breakdown = self._reduce_weekly_repetition(weekly_breakdown, duration_weeks)
            weekly_breakdown = self._enforce_weekly_expected_level_guard(
                weekly_breakdown, used_learning_targets, duration_weeks
            )

            # Rebuild resources with specific queries
            for week in weekly_breakdown:
                resource_queries = self._build_specific_resource_queries_for_week(
                    week=week,
                    duration_weeks=duration_weeks,
                    available_hours_per_week=available_hours_per_week,
                    feedback_intents=feedback_intents_str,
                )

                # Also use LLM-provided queries if they're specific
                llm_queries = week.pop("resource_queries", []) or []
                merged_queries = self._merge_resource_queries(llm_queries, resource_queries)

                level_info = self._infer_week_levels_from_targets(
                    week=week,
                    used_learning_targets=used_learning_targets,
                    fallback_target_level=fallback_target_level
                )

                week["resources"] = await self.resource_search_service.search_resources(
                    resource_queries=merged_queries,
                    max_per_week=4,
                    current_level=level_info["current_level"],
                    target_level=level_info["target_level"],
                    available_hours_per_week=available_hours_per_week,
                    week_number=week.get("week_number"),
                    duration_weeks=duration_weeks,
                    context_keywords=week.get("focus_skills", []) + [week.get("topic", "")],
                )

            regenerated_plan = {
                **previous_plan,
                "plan_summary": plan_data.get("plan_summary", previous_plan.get("plan_summary", "")),
                "improvement_summary": plan_data.get("improvement_summary", previous_plan.get("improvement_summary", "")),
                "weekly_breakdown": weekly_breakdown,
                "regenerated": True,
                "regeneration_mode": regeneration_mode,
                "feedback_intents": feedback_intents_str,
            }

            # Validate that feedback was meaningfully applied
            feedback_validation = self._validate_feedback_application(
                previous_breakdown=previous_plan.get("weekly_breakdown", []),
                regenerated_breakdown=weekly_breakdown,
                feedback_intents=feedback_intents_str,
            )
            regenerated_plan["feedback_application_quality"] = feedback_validation
            
            logger.info(
                "Plan regenerated successfully. Feedback applied: %s. Quality: %s%%",
                ", ".join(feedback_intents_str),
                feedback_validation.get("quality_score", 0)
            )

            # Preserve metadata from original plan
            for field in [
                "available_hours_per_week", "study_intensity", "planning_mode",
                "current_average_level", "current_track_score", "final_expected_level",
                "final_track_score", "final_skill_levels_after_plan", "learning_targets",
                "merged_learning_targets", "used_learning_targets", "deferred_learning_targets",
                "analysis_snapshot", "skill_schedule",
            ]:
                if field in previous_plan:
                    regenerated_plan[field] = previous_plan[field]

            return regenerated_plan

        except Exception as e:
            logger.warning("Plan regeneration failed, using fallback. Reason: %s", e)
            return await self._build_fallback_regenerated_plan(
                previous_plan=previous_plan,
                feedback_intents=feedback_intents_str,
                regeneration_mode=regeneration_mode,
            )

    def _build_compact_context(self, previous_plan: Dict[str, Any]) -> Dict[str, Any]:
        """
        Build compact context for LLM — include what's needed, skip raw resources.
        """
        weekly_breakdown_compact = []
        for week in (previous_plan.get("weekly_breakdown") or []):
            weekly_breakdown_compact.append({
                "week_number": week.get("week_number"),
                "focus_skills": week.get("focus_skills", []),
                "topic": week.get("topic", ""),
                "description": week.get("description", ""),
                "learning_outcomes": week.get("learning_outcomes", []),
                "expected_level_after_week": week.get("expected_level_after_week", "beginner"),
                # Omit "resources" to save tokens
            })

        return {
            "track_name": previous_plan.get("track_name"),
            "duration_weeks": previous_plan.get("duration_weeks"),
            "available_hours_per_week": previous_plan.get("available_hours_per_week"),
            "study_intensity": previous_plan.get("study_intensity"),
            "planning_mode": previous_plan.get("planning_mode"),
            "current_average_level": previous_plan.get("current_average_level"),
            "final_expected_level": previous_plan.get("final_expected_level"),
            "used_learning_targets": previous_plan.get("used_learning_targets", []),
            "deferred_learning_targets": previous_plan.get("deferred_learning_targets", []),
            "weekly_breakdown": weekly_breakdown_compact,
        }

    def _soft_validate_and_fix(
        self,
        weekly_breakdown: List[Dict[str, Any]],
        duration_weeks: int,
        used_learning_targets: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Soft validation: fix issues instead of raising exceptions.
        Only raise for truly unrecoverable problems.
        """
        if not isinstance(weekly_breakdown, list) or not weekly_breakdown:
            raise ValueError("Regenerated plan is missing weekly breakdown")

        # Fix week count mismatch
        if len(weekly_breakdown) != duration_weeks:
            if len(weekly_breakdown) > duration_weeks:
                logger.warning("Trimming regenerated plan from %s to %s weeks", len(weekly_breakdown), duration_weeks)
                weekly_breakdown = weekly_breakdown[:duration_weeks]
            else:
                # Pad with last week copy
                logger.warning("Padding regenerated plan from %s to %s weeks", len(weekly_breakdown), duration_weeks)
                while len(weekly_breakdown) < duration_weeks:
                    last = dict(weekly_breakdown[-1])
                    last["week_number"] = len(weekly_breakdown) + 1
                    last["topic"] = f"Review & Consolidation: {last.get('focus_skills', ['Skills'])[0]}"
                    weekly_breakdown.append(last)

        # Fix week numbers
        for i, week in enumerate(weekly_breakdown, start=1):
            if not isinstance(week, dict):
                weekly_breakdown[i - 1] = {"week_number": i, "focus_skills": [], "topic": "TBD", "description": "TBD", "learning_outcomes": [], "expected_level_after_week": "beginner", "resource_queries": []}
                continue
            week["week_number"] = i

            # Ensure required fields
            if not week.get("focus_skills"):
                week["focus_skills"] = [used_learning_targets[0].get("skill_name", "Core Skill")] if used_learning_targets else ["Core Skill"]
            if not week.get("topic"):
                week["topic"] = f"Week {i}: {week['focus_skills'][0]}"
            if not week.get("description"):
                week["description"] = f"This week focuses on {week['focus_skills'][0]}."
            if not week.get("learning_outcomes"):
                week["learning_outcomes"] = [f"Progress in {week['focus_skills'][0]}"]
            if "expected_level_after_week" not in week:
                week["expected_level_after_week"] = "beginner"

        return weekly_breakdown

    def _build_specific_resource_queries_for_week(
        self,
        week: Dict[str, Any],
        duration_weeks: int,
        available_hours_per_week: int,
        feedback_intents: List[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build hyper-specific resource queries using the week's actual topic.
        STRONGLY adjusted based on feedback intents to ensure meaningful application.
        """
        topic = (week.get("topic") or "").strip()
        focus_skills = week.get("focus_skills", []) or []
        primary_skill = focus_skills[0] if focus_skills else topic
        week_number = int(week.get("week_number", 1) or 1)
        stage_ratio = week_number / max(duration_weeks, 1)

        # Extract topic keywords (remove skill name prefix)
        topic_clean = topic.replace(primary_skill, "").strip(" —:-")
        topic_kw = " ".join(topic_clean.split()[:5]) if topic_clean else primary_skill

        # Difficulty hint (default)
        if stage_ratio <= 0.33:
            difficulty = "beginner introduction"
            practice_type = "practice"
            extra_type = "course"
            youtube_type = "tutorial"
        elif stage_ratio <= 0.75:
            difficulty = "intermediate"
            practice_type = "practice"
            extra_type = "article"
            youtube_type = "walkthrough"
        else:
            difficulty = "advanced"
            practice_type = "project"
            extra_type = "project"
            youtube_type = "advanced patterns"

        # STRONG adjustment based on feedback
        feedback_intents = feedback_intents or []
        
        if "more_practical" in feedback_intents or "more_projects" in feedback_intents:
            practice_type = "project"
            extra_type = "project"
            youtube_type = "implementation"
            
        if "more_advanced" in feedback_intents:
            difficulty = "advanced" if stage_ratio > 0.2 else "intermediate"
            youtube_type = "advanced patterns"
            
        if "simpler_basics" in feedback_intents:
            if stage_ratio <= 0.4:
                difficulty = "beginner fundamentals"
                youtube_type = "basics introduction"
            else:
                difficulty = "beginner intermediate"
                
        if "more_theory" in feedback_intents:
            extra_type = "docs"
            youtube_type = "concepts explanation"
            
        if "more_examples" in feedback_intents:
            youtube_type = "walkthrough with examples"
            extra_type = "article"
            
        if "faster_progress" in feedback_intents and stage_ratio <= 0.5:
            difficulty = "intermediate"
            practice_type = "project"

        if available_hours_per_week <= 5:
            practice_type = "practice"
            difficulty = difficulty.replace("advanced", "intermediate")

        return [
            {
                "title": f"{primary_skill}: {topic_kw} — {youtube_type.title()}",
                "query": f"{primary_skill} {topic_kw} {difficulty} {youtube_type} step-by-step",
                "type": "youtube",
            },
            {
                "title": f"{primary_skill}: {topic_kw} — Official Reference",
                "query": f"{primary_skill} {topic_kw} official documentation API reference guide",
                "type": "docs",
            },
            {
                "title": f"{primary_skill}: {topic_kw} — Hands-on Implementation",
                "query": f"{primary_skill} {topic_kw} {practice_type} hands-on challenges exercises",
                "type": practice_type,
            },
            {
                "title": f"{primary_skill}: {topic_kw} — Supplementary",
                "query": f"{primary_skill} {topic_kw} {extra_type} best practices case study",
                "type": extra_type,
            },
        ]

    def _merge_resource_queries(
        self,
        llm_queries: List[Dict[str, Any]],
        fallback_queries: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Prefer LLM queries if they're specific enough (>3 words), 
        otherwise use fallback queries.
        """
        merged = []
        used_types = set()

        # First, take good LLM queries
        for q in llm_queries:
            query = (q.get("query") or "").strip()
            q_type = (q.get("type") or "article").lower()
            # Consider specific if query has >3 meaningful words
            is_specific = len(query.split()) > 3
            if is_specific and q_type not in used_types:
                merged.append(q)
                used_types.add(q_type)

        # Fill remaining with fallback
        for q in fallback_queries:
            q_type = (q.get("type") or "article").lower()
            if q_type not in used_types:
                merged.append(q)
                used_types.add(q_type)

        return merged[:4]

    # ============================================================
    # HELPERS (kept from original with minor improvements)
    # ============================================================

    def _normalize_intent(self, intent: Any) -> str:
        if hasattr(intent, "value"):
            return str(intent.value).strip().lower()
        return str(intent).split(".")[-1].strip().lower()

    def _infer_week_levels_from_targets(
        self,
        week: Dict[str, Any],
        used_learning_targets: List[Dict[str, Any]],
        fallback_target_level: str = "intermediate"
    ) -> Dict[str, str]:
        focus_skills = week.get("focus_skills", []) or []
        primary_skill = focus_skills[0].strip().lower() if focus_skills else ""
        for target in used_learning_targets or []:
            if (target.get("skill_name") or "").strip().lower() == primary_skill:
                return {
                    "current_level": self._normalize_level(target.get("current_level")),
                    "target_level": self._normalize_level(target.get("target_level")),
                }
        return {
            "current_level": "beginner",
            "target_level": self._normalize_level(
                fallback_target_level if fallback_target_level in ("beginner", "intermediate") else "intermediate"
            ),
        }

    def _normalize_level(self, level: Any) -> str:
        value = str(level or "beginner").strip().lower()
        return value if value in self.LEVEL_VALUES else "beginner"

    def _safe_parse_json(self, raw_response: Any) -> Dict[str, Any]:
        if isinstance(raw_response, dict):
            return raw_response
        if not raw_response:
            return {}
        cleaned = str(raw_response).strip().replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(cleaned)
        except Exception:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start != -1 and end != -1 and end > start:
                try:
                    return json.loads(cleaned[start:end + 1])
                except Exception:
                    pass
        raise ValueError("Failed to parse regenerated plan JSON")

    def _reduce_weekly_repetition(
        self,
        weekly_breakdown: List[Dict[str, Any]],
        duration_weeks: int,
    ) -> List[Dict[str, Any]]:
        if not weekly_breakdown:
            return weekly_breakdown
        seen = set()
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
                    week["topic"] = f"Project Milestone: {main_skill}"
                desc = (week.get("description") or "").strip()
                if desc:
                    week["description"] = f"{desc} This week builds on prior learning with a distinct milestone."
            seen.add((
                tuple(sorted(s.lower() for s in focus_skills)),
                (week.get("topic") or "").strip().lower(),
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
                logger.warning("Week %s (%s): level '%s' → '%s'", week_number, primary_skill, declared_level, allowed)
                week["expected_level_after_week"] = allowed
        return weekly_breakdown

    def _validate_feedback_application(
        self,
        previous_breakdown: List[Dict[str, Any]],
        regenerated_breakdown: List[Dict[str, Any]],
        feedback_intents: List[str],
    ) -> Dict[str, Any]:
        """
        Validate that feedback was meaningfully applied to the plan.
        Returns quality assessment.
        """
        if not feedback_intents:
            return {"quality_score": 0, "is_meaningful": False, "issues": ["No feedback intents provided"]}

        quality_metrics = {
            "topics_changed": 0,
            "descriptions_changed": 0,
            "resources_changed": 0,
            "total_changes": 0,
            "issues": [],
        }

        min_changes_required = {
            "more_advanced": 0.6,  # At least 60% of weeks should change
            "more_practical": 0.7,  # Higher threshold for practical
            "less_repetition": 0.5,
            "focus_selected_skills": 0.4,
            "faster_progress": 0.5,
            "simpler_basics": 0.6,
            "more_projects": 0.7,
            "more_theory": 0.6,
            "better_structure": 0.8,  # Highest threshold
            "more_examples": 0.4,
        }

        prev_map = {w.get("week_number"): w for w in (previous_breakdown or [])}
        
        for regen_week in regenerated_breakdown or []:
            week_num = regen_week.get("week_number", 0)
            prev_week = prev_map.get(week_num, {})
            
            if not prev_week:
                continue

            # Check topic change
            if (prev_week.get("topic") or "").strip().lower() != (regen_week.get("topic") or "").strip().lower():
                quality_metrics["topics_changed"] += 1

            # Check description change
            if (prev_week.get("description") or "").strip()[:50] != (regen_week.get("description") or "").strip()[:50]:
                quality_metrics["descriptions_changed"] += 1

            # Check resource queries change
            prev_resources = prev_week.get("resource_queries", []) or []
            regen_resources = regen_week.get("resource_queries", []) or []
            
            if len(prev_resources) != len(regen_resources):
                quality_metrics["resources_changed"] += 1
            else:
                prev_queries = [r.get("query", "") for r in prev_resources]
                regen_queries = [r.get("query", "") for r in regen_resources]
                if prev_queries != regen_queries:
                    quality_metrics["resources_changed"] += 1

        total_weeks = len(regenerated_breakdown) or 1
        quality_metrics["total_changes"] = quality_metrics["topics_changed"] + quality_metrics["descriptions_changed"] + quality_metrics["resources_changed"]

        # Calculate quality score based on feedback intent
        change_ratio = (quality_metrics["topics_changed"] + quality_metrics["descriptions_changed"]) / total_weeks
        
        # Determine minimum required change ratio
        min_ratio = 0.5  # Default 50%
        for intent in feedback_intents:
            intent_min = min_changes_required.get(intent, 0.5)
            if intent_min > min_ratio:
                min_ratio = intent_min

        is_meaningful = change_ratio >= min_ratio
        
        if not is_meaningful:
            quality_metrics["issues"].append(
                f"Insufficient changes detected. {change_ratio*100:.0f}% weeks changed, required ≥{min_ratio*100:.0f}%"
            )

        # Bonus: check if resources were adjusted for feedback
        if "more_practical" in feedback_intents:
            project_count = sum(1 for w in regenerated_breakdown for r in w.get("resource_queries", []) if r.get("type") in ("project", "practice"))
            if project_count < len(regenerated_breakdown) * 0.4:
                quality_metrics["issues"].append("More practical feedback: insufficient project/practice resources")

        quality_metrics["is_meaningful"] = is_meaningful
        quality_metrics["quality_score"] = int(min(100, change_ratio * 100 + (25 if is_meaningful else -25)))

        return quality_metrics


    def _fallback_week_level(self, current_level, target_level, week_number, duration_weeks):
        current_level = self._normalize_level(current_level)
        target_level = self._normalize_level(target_level)
        current_val = self.LEVEL_VALUES.get(current_level, 0)
        target_val = self.LEVEL_VALUES.get(target_level, 1)
        if current_level == "none":
            if week_number == 1:
                level = "beginner"
            elif week_number <= max(2, duration_weeks // 3):
                level = "beginner"
            elif week_number <= max(4, (duration_weeks * 2) // 3):
                level = "intermediate"
            else:
                level = "advanced"
        elif current_level == "beginner":
            level = "intermediate" if week_number <= 2 else "advanced"
        else:
            level = "advanced"
        level_val = self.LEVEL_VALUES.get(level, 1)
        if level_val > target_val:
            return target_level
        return level

    async def _build_fallback_regenerated_plan(
        self,
        previous_plan: Dict[str, Any],
        feedback_intents: List[str],
        regeneration_mode: str,
    ) -> Dict[str, Any]:
        duration_weeks = int(previous_plan.get("duration_weeks", 1) or 1)
        used_learning_targets = previous_plan.get("used_learning_targets", []) or []
        available_hours_per_week = int(previous_plan.get("available_hours_per_week", 6) or 6)

        if not used_learning_targets:
            raise ValueError("Cannot build fallback regenerated plan without used_learning_targets")

        # Try to reuse skill_schedule if available
        skill_schedule = previous_plan.get("skill_schedule")

        if skill_schedule:
            weeks = self._build_weeks_from_schedule(
                skill_schedule=skill_schedule,
                duration_weeks=duration_weeks,
                feedback_intents=feedback_intents,
            )
        else:
            weeks = self._build_weeks_from_targets(
                used_learning_targets=used_learning_targets,
                duration_weeks=duration_weeks,
                feedback_intents=feedback_intents,
            )

        weeks = self._reduce_weekly_repetition(weeks, duration_weeks)
        weeks = self._enforce_weekly_expected_level_guard(weeks, used_learning_targets, duration_weeks)

        for week in weeks:
            resource_queries = self._build_specific_resource_queries_for_week(
                week=week,
                duration_weeks=duration_weeks,
                available_hours_per_week=available_hours_per_week,
                feedback_intents=feedback_intents,
            )

            level_info = self._infer_week_levels_from_targets(
                week=week,
                used_learning_targets=used_learning_targets,
                fallback_target_level=previous_plan.get("final_expected_level", "intermediate"),
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

        return {
            **previous_plan,
            "plan_summary": previous_plan.get("plan_summary", ""),
            "improvement_summary": previous_plan.get("improvement_summary", ""),
            "weekly_breakdown": weeks,
            "regenerated": True,
            "regeneration_mode": regeneration_mode,
            "feedback_intents": feedback_intents,
        }

    def _build_weeks_from_schedule(
        self,
        skill_schedule: List[Dict[str, Any]],
        duration_weeks: int,
        feedback_intents: List[str],
    ) -> List[Dict[str, Any]]:
        """Rebuild weeks from pre-computed skill_schedule (if available in previous_plan)."""
        weeks = []
        is_practical = "more_practical" in feedback_intents
        is_advanced = "more_advanced" in feedback_intents

        for entry in skill_schedule:
            skill_name = entry["skill_name"]
            current_level = entry.get("current_level", "none")
            target_level = entry.get("target_level", "beginner")
            learning_mode = entry.get("learning_mode", "learn_from_scratch")

            for guide in entry.get("subtopic_guide", []):
                week_number = entry["week_numbers"][guide["week_offset"] - 1]
                subtopic = guide["subtopic_focus"]
                expected_level = guide["expected_level"]

                # Apply feedback to topic/description
                if is_practical:
                    subtopic_enhanced = f"{subtopic} — hands-on project"
                    description_suffix = " Emphasizes real-world implementation and practical exercises."
                elif is_advanced:
                    subtopic_enhanced = f"{subtopic} — advanced patterns"
                    description_suffix = f" Focuses on {target_level}-level depth and production scenarios."
                else:
                    subtopic_enhanced = subtopic
                    description_suffix = ""

                if learning_mode == "level_up":
                    topic = f"{skill_name}: {subtopic_enhanced} ({current_level} → {target_level})"
                    description = (
                        f"This week deepens {skill_name} toward {target_level}: {subtopic}. "
                        f"Skip fundamentals — focus on {target_level} patterns and real-world usage.{description_suffix}"
                    )
                else:
                    topic = f"{skill_name}: {subtopic_enhanced}"
                    description = (
                        f"This week covers {skill_name}: {subtopic}. "
                        f"Build from {current_level} toward {expected_level} through structured practice.{description_suffix}"
                    )

                subtopic_kw = " ".join(subtopic.replace("&", "and").split()[:4])
                weeks.append({
                    "week_number": week_number,
                    "focus_skills": [skill_name],
                    "topic": topic,
                    "description": description,
                    "learning_outcomes": [
                        f"Apply {skill_name}: {subtopic} at {expected_level} level",
                        f"Complete hands-on practice for {subtopic_kw}"
                    ],
                    "expected_level_after_week": expected_level,
                    "resources": [],
                })

        weeks.sort(key=lambda w: w["week_number"])
        return weeks

    def _build_weeks_from_targets(
        self,
        used_learning_targets: List[Dict[str, Any]],
        duration_weeks: int,
        feedback_intents: List[str],
    ) -> List[Dict[str, Any]]:
        """Fallback week builder without skill_schedule."""
        stages = [
            "Foundations & mental model",
            "Guided implementation",
            "Applied practice",
            "Project integration",
        ]
        weeks = []
        targets_count = len(used_learning_targets)
        skill_counters: Dict[int, int] = {}

        for i in range(duration_weeks):
            target = used_learning_targets[i % targets_count]
            skill_id = target.get("skill_id", i)
            skill_name = target.get("skill_name", "Skill")
            current_level = self._normalize_level(target.get("current_level"))
            target_level = self._normalize_level(target.get("target_level"))
            learning_mode = target.get("learning_mode", "learn_from_scratch")

            stage_idx = skill_counters.get(skill_id, 0) % len(stages)
            skill_counters[skill_id] = stage_idx + 1
            stage = stages[stage_idx]

            if learning_mode == "level_up":
                topic = f"{skill_name}: {stage} ({current_level} → {target_level})"
                description = (
                    f"This week deepens {skill_name} from {current_level} toward {target_level}. "
                    f"Focus: {stage}."
                )
            else:
                topic = f"{skill_name}: {stage}"
                description = (
                    f"This week introduces {skill_name} through {stage}. "
                    f"Build toward {target_level} level."
                )

            weeks.append({
                "week_number": i + 1,
                "focus_skills": [skill_name],
                "topic": topic,
                "description": description,
                "learning_outcomes": [
                    f"Progress {skill_name} through {stage}",
                    f"Reach {self._fallback_week_level(current_level, target_level, i + 1, duration_weeks)} level"
                ],
                "expected_level_after_week": self._fallback_week_level(
                    current_level, target_level, i + 1, duration_weeks
                ),
                "resources": [],
            })

        return weeks