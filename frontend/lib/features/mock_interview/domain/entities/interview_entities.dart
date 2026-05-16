// ─── Session Types ────────────────────────────────────────────────────────────

enum InterviewSessionType { behavioral, technical }

// ─── Interview Question Entity ────────────────────────────────────────────────

class InterviewQuestionEntity {
  final String questionId;
  final String questionText;

  const InterviewQuestionEntity({
    required this.questionId,
    required this.questionText,
  });
}

// ─── Interview Session Entity ─────────────────────────────────────────────────

class InterviewSessionEntity {
  final String sessionId;
  final String sasToken;
  final String blobUrl;
  final DateTime sasExpiresAt;
  final List<InterviewQuestionEntity> questions;
  final InterviewSessionType sessionType;

  const InterviewSessionEntity({
    required this.sessionId,
    required this.sasToken,
    required this.blobUrl,
    required this.sasExpiresAt,
    required this.questions,
    required this.sessionType,
  });
}

// ─── Interview Feedback Summary Entity ───────────────────────────────────────

class InterviewFeedbackSummary {
  final String sessionId;
  final String roleName;
  final InterviewSessionType sessionType;
  final DateTime createdAt;
  // Shown in the card — null until report is fetched
  final int? score;
  final String recommendation;

  const InterviewFeedbackSummary({
    required this.sessionId,
    required this.roleName,
    required this.sessionType,
    required this.createdAt,
    this.score,
    this.recommendation = '',
  });
}

// ─── Interview Feedback Detail Entity ────────────────────────────────────────

class InterviewFeedbackDetailEntity {
  final String sessionId;
  final String roleName;
  final InterviewSessionType sessionType;
  final DateTime createdAt;
  final int? score;
  final String recommendation;
  final List<String> strongPoints;
  final List<String> areasForImprovement;
  final String suggestions;
  final String? behavioralReport;
  final String? technicalReport;

  const InterviewFeedbackDetailEntity({
    required this.sessionId,
    required this.roleName,
    required this.sessionType,
    required this.createdAt,
    this.score,
    this.recommendation = '',
    required this.strongPoints,
    required this.areasForImprovement,
    required this.suggestions,
    this.behavioralReport,
    this.technicalReport,
  });
}

// ─── Available Roles ──────────────────────────────────────────────────────────

class InterviewRole {
  final String roleId;
  final String roleName;

  const InterviewRole({required this.roleId, required this.roleName});
}
