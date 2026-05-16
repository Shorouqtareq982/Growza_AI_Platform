import 'dart:async';
import 'dart:io';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../data/datasources/interview_roles.dart';
import '../../data/repositories/mock_interview_repository_impl.dart';
import '../../domain/entities/interview_entities.dart';
import '../../../mock_interview/presentation/providers/notification_helper.dart';
import '../../data/models/interview_models.dart';
import '../../../alerts/data/datasources/alerts_local_datasource.dart';

// ─── Enums ────────────────────────────────────────────────────────────────────

enum InterviewSessionStatus {
  idle,
  starting,
  active,
  paused,
  finished,
  uploading,
  error,
}

enum FeedbackLoadStatus { idle, loading, success, error }

// ─── State ────────────────────────────────────────────────────────────────────

class MockInterviewState {
  final List<InterviewFeedbackSummary> feedbackList;
  final FeedbackLoadStatus feedbackStatus;
  final InterviewSessionEntity? session;
  final InterviewSessionStatus sessionStatus;
  final int currentQuestionIndex;
  final int remainingSeconds;
  final List<int>? audioBytes;
  final bool isLoadingAudio;
  final bool waitingForAudio;
  final InterviewFeedbackDetailEntity? feedbackDetail;
  final bool isLoadingDetail;
  final double uploadProgress;
  final String? errorMessage;

  const MockInterviewState({
    this.feedbackList = const [],
    this.feedbackStatus = FeedbackLoadStatus.idle,
    this.session,
    this.sessionStatus = InterviewSessionStatus.idle,
    this.currentQuestionIndex = 0,
    this.remainingSeconds = 30,
    this.audioBytes,
    this.isLoadingAudio = false,
    this.waitingForAudio = false,
    this.feedbackDetail,
    this.isLoadingDetail = false,
    this.uploadProgress = 0.0,
    this.errorMessage,
  });

  MockInterviewState copyWith({
    List<InterviewFeedbackSummary>? feedbackList,
    FeedbackLoadStatus? feedbackStatus,
    InterviewSessionEntity? session,
    InterviewSessionStatus? sessionStatus,
    int? currentQuestionIndex,
    int? remainingSeconds,
    List<int>? audioBytes,
    bool? isLoadingAudio,
    bool? waitingForAudio,
    InterviewFeedbackDetailEntity? feedbackDetail,
    bool? isLoadingDetail,
    double? uploadProgress,
    String? errorMessage,
    bool clearError = false,
    bool clearSession = false,
    bool clearAudio = false,
    bool clearDetail = false,
  }) {
    return MockInterviewState(
      feedbackList: feedbackList ?? this.feedbackList,
      feedbackStatus: feedbackStatus ?? this.feedbackStatus,
      session: clearSession ? null : (session ?? this.session),
      sessionStatus: sessionStatus ?? this.sessionStatus,
      currentQuestionIndex: currentQuestionIndex ?? this.currentQuestionIndex,
      remainingSeconds: remainingSeconds ?? this.remainingSeconds,
      audioBytes: clearAudio ? null : (audioBytes ?? this.audioBytes),
      isLoadingAudio: isLoadingAudio ?? this.isLoadingAudio,
      waitingForAudio: waitingForAudio ?? this.waitingForAudio,
      feedbackDetail:
          clearDetail ? null : (feedbackDetail ?? this.feedbackDetail),
      isLoadingDetail: isLoadingDetail ?? this.isLoadingDetail,
      uploadProgress: uploadProgress ?? this.uploadProgress,
      errorMessage: clearError ? null : (errorMessage ?? this.errorMessage),
    );
  }
}

// ─── Notifier ─────────────────────────────────────────────────────────────────

class MockInterviewNotifier extends StateNotifier<MockInterviewState> {
  final MockInterviewRepositoryImpl _repo;
  Timer? _questionTimer;

  static const String _notifiedSessionsKey = 'notified_interview_sessions';

  MockInterviewNotifier(this._repo) : super(const MockInterviewState());

  // ── Load Feedback List ─────────────────────────────────────────────────────

  Future<void> loadFeedbackList() async {
    state = state.copyWith(
        feedbackStatus: FeedbackLoadStatus.loading, clearError: true);
    try {
      final sessions = await _repo.getLocalSessions();

      final readySessions = <Map<String, dynamic>>[];
      final Set<String> seenIds = {};

      for (final s in sessions) {
        final sessionId = s['session_id'] as String;

        if (seenIds.contains(sessionId)) continue;
        seenIds.add(sessionId);

        final sessionType = s['session_type'] as String? ?? '';
        try {
          String report;
          if (sessionType == 'technical') {
            report = await _repo.getTechnicalReport(sessionId);
          } else {
            report = await _repo.getBehavioralReport(sessionId);
          }
          if (report.isNotEmpty) {
            readySessions.add(s);
          }
        } catch (_) {}
      }

      final summaries = readySessions
          .map((s) => InterviewFeedbackSummary(
                sessionId: s['session_id'] as String,
                roleName: s['role_name'] as String,
                sessionType: (s['session_type'] as String) == 'technical'
                    ? InterviewSessionType.technical
                    : InterviewSessionType.behavioral,
                createdAt: DateTime.tryParse(s['created_at'] as String) ??
                    DateTime.now(),
              ))
          .toList();

      state = state.copyWith(
        feedbackList: summaries,
        feedbackStatus: FeedbackLoadStatus.success,
      );
    } catch (e) {
      state = state.copyWith(
        feedbackStatus: FeedbackLoadStatus.error,
        errorMessage: _parseError(e),
      );
    }
  }

  // ── Load Feedback Detail ───────────────────────────────────────────────────

  Future<void> loadFeedbackDetail(String sessionId) async {
    state = state.copyWith(isLoadingDetail: true, clearError: true);
    try {
      final sessions = await _repo.getLocalSessions();
      final session = sessions.firstWhere(
        (s) => s['session_id'] == sessionId,
        orElse: () => {},
      );

      final sessionType = (session['session_type'] as String?) == 'technical'
          ? InterviewSessionType.technical
          : InterviewSessionType.behavioral;
      final roleName = session['role_name'] as String? ?? '';

      String? behavioralReport;
      String? technicalReport;

      if (sessionType == InterviewSessionType.behavioral) {
        behavioralReport = await _repo.getBehavioralReport(sessionId);
      } else {
        technicalReport = await _repo.getTechnicalReport(sessionId);
      }

      final report = behavioralReport ?? technicalReport ?? '';
      final strongPoints = ReportParser.extractStrengths(report);
      final areasForImprovement = ReportParser.extractWeaknesses(report);
      final suggestions = ReportParser.extractSuggestions(report);

      final detail = InterviewFeedbackDetailEntity(
        sessionId: sessionId,
        roleName: roleName,
        sessionType: sessionType,
        createdAt: DateTime.tryParse(session['created_at'] as String? ?? '') ??
            DateTime.now(),
        strongPoints: strongPoints,
        areasForImprovement: areasForImprovement,
        suggestions: suggestions,
        behavioralReport: behavioralReport,
        technicalReport: technicalReport,
      );

      state = state.copyWith(feedbackDetail: detail, isLoadingDetail: false);
    } catch (e) {
      state = state.copyWith(
        isLoadingDetail: false,
        errorMessage: _parseError(e),
      );
    }
  }

  // ── Background Polling ─────────────────────────────────────────────────────

  Future<void> _pollForReport({
    required String sessionId,
    required String roleName,
    required InterviewSessionType sessionType,
    int maxAttempts = 30,
    int intervalSeconds = 15,
  }) async {
    for (int attempt = 0; attempt < maxAttempts; attempt++) {
      await Future.delayed(Duration(seconds: intervalSeconds));

      try {
        String report;
        if (sessionType == InterviewSessionType.technical) {
          report = await _repo.getTechnicalReport(sessionId);
        } else {
          report = await _repo.getBehavioralReport(sessionId);
        }

        if (report.isNotEmpty) {
          await _sendNotificationOnce(sessionId: sessionId, roleName: roleName);

          final alreadyInList =
              state.feedbackList.any((f) => f.sessionId == sessionId);
          if (!alreadyInList) {
            final sessions = await _repo.getLocalSessions();
            final sessionData = sessions.firstWhere(
              (s) => s['session_id'] == sessionId,
              orElse: () => {},
            );
            if (sessionData.isNotEmpty) {
              final newSummary = InterviewFeedbackSummary(
                sessionId: sessionId,
                roleName: roleName,
                sessionType: sessionType,
                createdAt: DateTime.tryParse(
                        sessionData['created_at'] as String? ?? '') ??
                    DateTime.now(),
              );
              state = state.copyWith(
                feedbackList: [newSummary, ...state.feedbackList],
              );
            }
          }
          return;
        }
      } catch (_) {}
    }

    await _repo.deleteLocalSession(sessionId);
  }

  // ── Notification  ──────────────────────────────

  Future<void> _sendNotificationOnce({
    required String sessionId,
    required String roleName,
  }) async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final notified = prefs.getStringList(_notifiedSessionsKey) ?? [];
      if (notified.contains(sessionId)) return;

      await NotificationHelper.showFeedbackReady(
        roleName: roleName,
        sessionId: sessionId,
      );

      await AlertsStore.instance.addInterviewFeedbackAlert(
        roleName: roleName,
        sessionId: sessionId,
      );

      notified.add(sessionId);
      await prefs.setStringList(_notifiedSessionsKey, notified);
    } catch (_) {}
  }

  // ── Delete Feedback ────────────────────────────────────────────────────────

  Future<void> deleteFeedback(String sessionId) async {
    try {
      await _repo.deleteLocalSession(sessionId);

      final prefs = await SharedPreferences.getInstance();
      final notified = prefs.getStringList(_notifiedSessionsKey) ?? [];
      notified.remove(sessionId);
      await prefs.setStringList(_notifiedSessionsKey, notified);

      final updated =
          state.feedbackList.where((f) => f.sessionId != sessionId).toList();
      state = state.copyWith(feedbackList: updated);
    } catch (e) {
      state = state.copyWith(errorMessage: _parseError(e));
    }
  }

  // ── Start Session ──────────────────────────────────────────────────────────

  Future<void> startSession({
    required String roleName,
    required String roleId,
    required InterviewSessionType sessionType,
  }) async {
    state = state.copyWith(
      sessionStatus: InterviewSessionStatus.starting,
      clearError: true,
      currentQuestionIndex: 0,
    );

    try {
      final userId = Supabase.instance.client.auth.currentUser?.id ?? '';

      InterviewSessionEntity session;
      if (sessionType == InterviewSessionType.behavioral) {
        session = await _repo.startBehavioralSession(
          roleName: roleName,
          userId: userId,
        );
      } else {
        session = await _repo.startTechnicalSession(
          roleName: roleName,
          userId: userId,
        );
      }

      await _repo.saveSessionLocally(
        sessionId: session.sessionId,
        roleName: roleName,
        sessionType: sessionType,
      );

      state = state.copyWith(
        session: session,
        sessionStatus: InterviewSessionStatus.active,
        remainingSeconds: 30,
        waitingForAudio: true,
      );

      if (session.questions.isNotEmpty) {
        await _loadQuestionAudio(session.questions.first.questionId);
      }
    } catch (e) {
      state = state.copyWith(
        sessionStatus: InterviewSessionStatus.error,
        errorMessage: _parseError(e),
      );
    }
  }

  // ── Timer ──────────────────────────────────────────────────────────────────

  void startQuestionTimer() {
    _questionTimer?.cancel();
    state = state.copyWith(waitingForAudio: false);
    _questionTimer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (state.sessionStatus != InterviewSessionStatus.active) {
        _questionTimer?.cancel();
        return;
      }
      final remaining = state.remainingSeconds - 1;
      if (remaining <= 0) {
        _questionTimer?.cancel();
        _advanceQuestion();
      } else {
        state = state.copyWith(remainingSeconds: remaining);
      }
    });
  }

  // ── Advance Question ───────────────────────────────────────────────────────

  void _advanceQuestion() {
    final session = state.session;
    if (session == null) return;
    final nextIndex = state.currentQuestionIndex + 1;

    if (nextIndex >= session.questions.length) {
      _questionTimer?.cancel();
      state = state.copyWith(
        sessionStatus: InterviewSessionStatus.finished,
        remainingSeconds: 0,
        waitingForAudio: false,
      );
      return;
    }

    state = state.copyWith(
      currentQuestionIndex: nextIndex,
      remainingSeconds: 30,
      clearAudio: true,
      waitingForAudio: true,
    );
    _loadQuestionAudioAndNotify(session.questions[nextIndex].questionId);
  }

  // ── Load Audio ─────────────────────────────────────────────────────────────

  Future<void> _loadQuestionAudio(String questionId) async {
    state = state.copyWith(isLoadingAudio: true);
    try {
      final bytes = await _repo.getQuestionAudio(questionId);
      if (bytes.isEmpty) {
        state = state.copyWith(isLoadingAudio: false, waitingForAudio: false);
        startQuestionTimer();
        return;
      }
      state = state.copyWith(audioBytes: bytes, isLoadingAudio: false);
    } catch (_) {
      state = state.copyWith(isLoadingAudio: false, waitingForAudio: false);
      startQuestionTimer();
    }
  }

  Future<void> _loadQuestionAudioAndNotify(String questionId) async {
    state = state.copyWith(isLoadingAudio: true);
    try {
      final bytes = await _repo.getQuestionAudio(questionId);
      if (bytes.isEmpty) {
        state = state.copyWith(isLoadingAudio: false, waitingForAudio: false);
        startQuestionTimer();
        return;
      }
      state = state.copyWith(audioBytes: bytes, isLoadingAudio: false);
    } catch (_) {
      state = state.copyWith(isLoadingAudio: false, waitingForAudio: false);
      startQuestionTimer();
    }
  }

  // ── Pause / Resume ─────────────────────────────────────────────────────────

  void pauseInterview() {
    _questionTimer?.cancel();
    state = state.copyWith(sessionStatus: InterviewSessionStatus.paused);
  }

  void resumeInterview() {
    state = state.copyWith(sessionStatus: InterviewSessionStatus.active);
    if (!state.waitingForAudio) {
      startQuestionTimer();
    }
  }

  void skipToNext() {
    _questionTimer?.cancel();
    _advanceQuestion();
  }

  void restartInterview() {
    _questionTimer?.cancel();
    final session = state.session;
    if (session == null) return;
    state = state.copyWith(
      sessionStatus: InterviewSessionStatus.active,
      currentQuestionIndex: 0,
      remainingSeconds: 30,
      clearAudio: true,
      waitingForAudio: true,
    );
    if (session.questions.isNotEmpty) {
      _loadQuestionAudio(session.questions.first.questionId);
    }
  }

  // ── Upload & Notify ────────────────────────────────────────────────────────

  Future<void> uploadAndNotify({
    required File mediaFile,
    required String roleName,
  }) async {
    final session = state.session;
    if (session == null) return;

    state = state.copyWith(
      sessionStatus: InterviewSessionStatus.uploading,
      uploadProgress: 0.0,
    );

    try {
      await _repo.uploadToAzure(
        file: mediaFile,
        blobUrl: session.blobUrl,
        sasToken: session.sasToken,
        sessionType: session.sessionType,
      );

      state = state.copyWith(uploadProgress: 0.8);

      await _repo.notifyUploadComplete(
        sessionId: session.sessionId,
        blobUrl: session.blobUrl,
      );

      state = state.copyWith(
        sessionStatus: InterviewSessionStatus.finished,
        uploadProgress: 1.0,
      );

      _pollForReport(
        sessionId: session.sessionId,
        roleName: roleName,
        sessionType: session.sessionType,
      );
    } catch (e) {
      state = state.copyWith(
        sessionStatus: InterviewSessionStatus.error,
        errorMessage: _parseError(e),
      );
    }
  }

  // ── Reset ──────────────────────────────────────────────────────────────────

  void resetSession() {
    _questionTimer?.cancel();
    state = state.copyWith(
      clearSession: true,
      sessionStatus: InterviewSessionStatus.idle,
      currentQuestionIndex: 0,
      clearAudio: true,
      clearError: true,
      uploadProgress: 0.0,
      waitingForAudio: false,
    );
  }

  void clearFeedbackDetail() => state = state.copyWith(clearDetail: true);

  InterviewRole? findRole(String jobTitle) =>
      InterviewRoles.findByName(jobTitle);

  String _parseError(Object e) {
    final msg = e.toString();
    if (msg.contains('SocketException') ||
        msg.contains('Failed host lookup') ||
        msg.contains('Connection refused')) {
      return 'No internet connection. Please check your network.';
    }
    if (msg.contains('TimeoutException') || msg.contains('timed out')) {
      return 'Connection timed out. Please try again.';
    }
    if (msg.contains('401') || msg.contains('Unauthorized')) {
      return 'Session expired. Please sign in again.';
    }
    if (msg.contains('404')) return 'Session not found.';
    if (msg.contains('500')) return 'Server error. Please try again later.';
    return 'Something went wrong. Please try again.';
  }

  @override
  void dispose() {
    _questionTimer?.cancel();
    super.dispose();
  }
}

// ─── Providers ────────────────────────────────────────────────────────────────

final mockInterviewRepositoryProvider = Provider<MockInterviewRepositoryImpl>(
  (ref) => MockInterviewRepositoryImpl(),
);

final mockInterviewProvider =
    StateNotifierProvider<MockInterviewNotifier, MockInterviewState>(
  (ref) => MockInterviewNotifier(
    ref.read(mockInterviewRepositoryProvider),
  ),
);
