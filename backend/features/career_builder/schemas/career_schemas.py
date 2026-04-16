"""
Career Planning Schemas
Final version for:
1) analyze
2) confirm skills
3) confirm time
4) generate plan
5) save plan
6) checkpoint assessments (32-week optimized plan)
7) capstone project tracking
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from uuid import UUID
from datetime import datetime
from enum import Enum


# =====================================================
# ENUMS
# =====================================================

class LevelEnum(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class SkillStatus(str, Enum):
    HAS = "has"
    MISSING = "missing"
    PARTIAL = "partial"


class CurrentLevel(str, Enum):
    NONE = "none"
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class FitStatus(str, Enum):
    GOOD_FIT = "good_fit"
    MODERATE_FIT = "moderate_fit"
    POOR_FIT = "poor_fit"


class StudyIntensity(str, Enum):
    LIGHT = "light"
    MODERATE = "moderate"
    INTENSIVE = "intensive"


class PlanFeedbackIntent(str, Enum):
    """Available feedback intents for plan regeneration"""
    MORE_ADVANCED = "more_advanced"
    MORE_PRACTICAL = "more_practical"
    LESS_REPETITION = "less_repetition"
    FOCUS_SELECTED = "focus_selected_skills"
    FASTER_PROGRESS = "faster_progress"
    SIMPLER_BASICS = "simpler_basics"


class CheckpointStatus(str, Enum):
    """Status of checkpoint assessment"""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PASSED = "passed"
    NEEDS_REVIEW = "needs_review"


class LearningPhase(str, Enum):
    """8 Learning phases in 32-week optimized plan"""
    FOUNDATION_1 = "foundation_1"
    FOUNDATION_2 = "foundation_2"
    INTERMEDIATE_1 = "intermediate_1"
    INTERMEDIATE_2 = "intermediate_2"
    INTEGRATION_1 = "integration_1"
    INTEGRATION_2 = "integration_2"
    CAPSTONE_1 = "capstone_1"
    CAPSTONE_2 = "capstone_2"


# =====================================================
# SKILL SCHEMAS
# =====================================================

class SkillBase(BaseModel):
    skill_id: int
    skill_name: str
    category: str


class SkillDetail(SkillBase):
    importance_weight: int
    beginner_weeks: int
    intermediate_weeks: int
    advanced_weeks: int


class SkillGap(BaseModel):
    skill_id: int
    skill_name: str
    status: SkillStatus
    current_level: CurrentLevel
    required_level: LevelEnum
    gap_score: float = Field(..., ge=0.0, le=1.0)
    importance_weight: int
    required_weeks: int
    is_core: Optional[bool] = True


class FitAnalysis(BaseModel):
    fit_status: FitStatus
    fit_score: float = Field(..., ge=0.0, le=100.0)
    missing_core_skills: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    can_generate_plan: bool


class SkillOverride(BaseModel):
    skill_id: int
    level: CurrentLevel


class ReviewableSkill(BaseModel):
    skill_id: int
    skill_name: str
    status: SkillStatus
    detected_level: Optional[CurrentLevel] = None
    confidence: float = Field(..., ge=0.0, le=1.0)
    needs_user_input: bool
    required_level: LevelEnum
    selected_by_default: bool


class SuggestedTarget(BaseModel):
    skill_id: int
    skill_name: str
    current_level: CurrentLevel
    suggested_target_level: LevelEnum
    target_reason: str
    learning_mode: str


class LearningTarget(BaseModel):
    skill_id: int
    skill_name: str
    current_level: CurrentLevel
    target_level: LevelEnum
    required_level: LevelEnum
    required_weeks: int
    importance_weight: int
    learning_mode: str


class MatchedSkillItem(BaseModel):
    skill_id: int
    skill_name: str
    category: str
    importance: int


class MissingSkillItem(BaseModel):
    skill_id: int
    skill_name: str
    category: str
    importance: int
    duration_weeks: int
    is_core: bool = True


# =====================================================
# TRACK SCHEMAS
# =====================================================

class TrackBase(BaseModel):
    track_id: int
    track_name: str
    description: str


class TrackSummary(TrackBase):
    total_skills: int
    min_beginner_weeks: int
    min_intermediate_weeks: int
    min_advanced_weeks: int
    avg_importance: float


class TrackWithSkills(TrackBase):
    skills: List[SkillDetail]


class TrackDropdownItem(BaseModel):
    track_id: int
    track_name: str
    description: Optional[str] = None


class TrackListResponse(BaseModel):
    tracks: List[TrackDropdownItem]
    total: int


# =====================================================
# ANALYSIS
# =====================================================

class ExtractedSkill(BaseModel):
    skill_name: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    matched_skill_id: Optional[int] = None


class AnalysisMetadata(BaseModel):
    match_percentage: float = Field(..., ge=0.0, le=100.0)
    matching_method: str
    analysis_quality: float = Field(..., ge=0.0, le=1.0)


class CVAnalysisResponse(BaseModel):
    status: str = "success"
    cv_id: UUID
    track_id: int
    track_name: str
    detected_level: LevelEnum
    level_confidence: float = Field(..., ge=0.0, le=1.0)
    extracted_skills: List[ExtractedSkill] = Field(default_factory=list)
    matched_skills_count: int
    skill_gaps: List[SkillGap] = Field(default_factory=list)
    reviewable_skills: List[ReviewableSkill] = Field(default_factory=list)
    fit_analysis: Optional[FitAnalysis] = None
    matched_skills: List[MatchedSkillItem] = Field(default_factory=list)
    missing_skills: List[MissingSkillItem] = Field(default_factory=list)
    metadata: Optional[AnalysisMetadata] = None


# =====================================================
# CONFIRM SKILLS
# =====================================================

class ConfirmSkillsRequest(BaseModel):
    cv_id: UUID
    track_id: int
    selected_skill_ids: List[int] = Field(default_factory=list)
    skill_overrides: List[SkillOverride] = Field(default_factory=list)


class ConfirmSkillsResponse(BaseModel):
    status: str = "success"
    cv_id: UUID
    track_id: int
    track_name: str
    detected_level: LevelEnum
    selected_skill_ids: List[int] = Field(default_factory=list)
    reviewable_skills: List[ReviewableSkill] = Field(default_factory=list)
    skill_gaps: List[SkillGap] = Field(default_factory=list)
    fit_analysis: Optional[FitAnalysis] = None
    metadata: Optional[AnalysisMetadata] = None


# =====================================================
# CONFIRM TIME
# =====================================================

class RealismInfo(BaseModel):
    requested_weeks: int
    available_hours_per_week: int
    study_intensity: StudyIntensity
    safe_min_weeks: int
    recommended_weeks: int
    is_below_safe: bool
    adjustment: str
    warning: Optional[str] = None


# =====================================================
# TIME GUIDANCE (NEW)
# =====================================================

class TimeGuidanceInfo(BaseModel):
    """Time guidance for learning planning"""
    minimum_weeks: int  # Selected skills only, none->beginner
    suitable_weeks: int  # Selected + current skills, intermediate for core
    maximum_weeks: int  # Selected + current skills, advanced for all
    
    study_intensity: StudyIntensity
    
    # Detailed breakdowns
    minimum_weeks_breakdown: Dict[str, int]
    suitable_weeks_breakdown: Dict[str, int]
    maximum_weeks_breakdown: Dict[str, int]


class ConfirmTimePreviewResponse(BaseModel):
    """Preview endpoint response - guidance only before user enters hours/weeks"""
    status: str = "success"
    cv_id: UUID
    track_id: int
    track_name: str
    detected_level: LevelEnum
    selected_skill_ids: List[int] = Field(default_factory=list)
    
    guidance_hours_per_week: int  # Default baseline for preview
    time_guidance: TimeGuidanceInfo
    
    guidance_message: str
    note: str


# =====================================================
# ADVANCED REALISM CHECK (ENHANCED)
# =====================================================

class SkillRealismAnalysis(BaseModel):
    """Per-skill realism analysis"""
    skill_name: str
    skill_id: int
    current_level: CurrentLevel
    target_level: LevelEnum
    learning_mode: str
    base_required_weeks: int
    importance_weight: int
    progression_multiplier: float
    importance_adjustment: float
    hours_adjustment: float
    calculated_weeks_for_this_skill: int


class EnhancedRealismCheckInfo(BaseModel):
    """Enhanced realism check with warnings and suggestions"""
    is_realistic: bool
    adjustment: str  # ok, tight, very_tight, unrealistic_too_short, excessive
    
    requested_weeks: int
    available_hours_per_week: int
    study_intensity: StudyIntensity
    
    calculated_minimum_weeks: int
    calculated_suitable_weeks: int
    calculated_maximum_weeks: int
    
    warnings: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    
    fit_percentage: float  # 0-100%


class ConfirmTimeRequest(BaseModel):
    cv_id: UUID
    track_id: int
    requested_weeks: int = Field(..., ge=1, le=104)
    available_hours_per_week: int = Field(..., ge=1, le=80)


class ConfirmTimeResponse(BaseModel):
    status: str = "success"
    cv_id: UUID
    track_id: int
    track_name: str
    detected_level: LevelEnum
    available_hours_per_week: int
    requested_weeks: int
    selected_skill_ids: List[int] = Field(default_factory=list)
    suggested_targets: List[SuggestedTarget] = Field(default_factory=list)
    confirmed_learning_targets: List[LearningTarget] = Field(default_factory=list)
    realism: EnhancedRealismCheckInfo  # Updated to enhanced version



# =====================================================
# PLAN GENERATION
# =====================================================

class PlanGenerateRequest(BaseModel):
    cv_id: UUID
    track_id: int
    duration_weeks: int = Field(..., ge=1, le=104)
    available_hours_per_week: int = Field(..., ge=1, le=80)


class PlanRegenerateRequest(BaseModel):
    previous_plan: Dict[str, Any]
    feedback_intents: List[PlanFeedbackIntent] = Field(
        ...,
        description="List of feedback intent types for plan regeneration",
        min_items=1
    )
    regeneration_mode: str = Field(default="full", description="Type of regeneration: full, partial, focused")


class ResourceItem(BaseModel):
    title: str
    url: str
    type: str


class WeeklyContent(BaseModel):
    week_number: int
    focus_skills: List[str] = Field(default_factory=list)
    topic: str
    description: str
    learning_outcomes: List[str] = Field(default_factory=list)
    expected_level_after_week: LevelEnum
    resources: List[ResourceItem] = Field(default_factory=list)


class GeneratedPlan(BaseModel):
    track_id: int
    track_name: str
    required_level: LevelEnum
    duration_weeks: int
    available_hours_per_week: int
    study_intensity: StudyIntensity
    planning_mode: str
    current_average_level: LevelEnum
    current_track_score: float
    final_expected_level: LevelEnum
    final_track_score: float
    plan_summary: str
    improvement_summary: str
    weekly_breakdown: List[WeeklyContent] = Field(default_factory=list)


class PlanGenerationResponse(BaseModel):
    status: str = "success"
    plan: GeneratedPlan
    learning_targets: List[LearningTarget] = Field(default_factory=list)
    deferred_learning_targets: List[Dict[str, Any]] = Field(default_factory=list)
    realism: Optional[RealismInfo] = None
    fit_analysis: Optional[FitAnalysis] = None
    warnings: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


# =====================================================
# SAVE PLAN
# =====================================================

class SavePlanRequest(BaseModel):
    user_id: UUID
    cv_id: UUID
    track_id: int
    detected_level: LevelEnum
    confirmed_level: LevelEnum
    duration_weeks: int
    available_hours_per_week: Optional[int] = Field(default=None, ge=1, le=80)
    skill_gaps: List[SkillGap] = Field(default_factory=list)
    weekly_content: List[WeeklyContent] = Field(default_factory=list)


class SavePlanResponse(BaseModel):
    plan_id: int
    message: str
    created_at: datetime


# =====================================================
# USER PLANS
# =====================================================

class UserPlanSummary(BaseModel):
    plan_id: int
    track_name: str
    level: LevelEnum
    duration_weeks: int
    progress_percentage: float
    created_at: datetime
    updated_at: datetime


class UserPlansResponse(BaseModel):
    user_id: UUID
    plans: List[UserPlanSummary]
    total_plans: int


# =====================================================
# ERRORS
# =====================================================

class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None
    error_code: Optional[str] = None