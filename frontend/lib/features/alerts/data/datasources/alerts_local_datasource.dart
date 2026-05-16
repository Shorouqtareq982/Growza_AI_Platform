import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../../domain/entities/alert_entity.dart';
import '../models/alert_model.dart';

abstract class AlertsLocalDataSource {
  Future<List<AlertModel>> fetchAlerts();
  Future<List<AlertModel>> markAllRead();
  Future<List<AlertModel>> markRead(String id);
  Future<List<AlertModel>> addAlert(AlertModel alert);
}

class AlertsLocalDataSourceImpl implements AlertsLocalDataSource {
  static const String _key = 'growza_alerts';

  // ─── helpers ────────────────────────────────────────────────────────────────

  Future<List<AlertModel>> _load() async {
    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_key);

    if (raw == null || raw.trim().isEmpty) return [];

    try {
      final list = jsonDecode(raw) as List<dynamic>;
      return list
          .whereType<Map<String, dynamic>>()
          .map(AlertModel.fromJson)
          .toList();
    } catch (_) {
      return [];
    }
  }

  Future<void> _save(List<AlertModel> alerts) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
      _key,
      jsonEncode(alerts.map((a) => a.toJson()).toList()),
    );
  }

  // ─── public API ─────────────────────────────────────────────────────────────

  @override
  Future<List<AlertModel>> fetchAlerts() async {
    final alerts = await _load();
    alerts.sort((a, b) => b.createdAt.compareTo(a.createdAt));
    return alerts;
  }

  @override
  Future<List<AlertModel>> markAllRead() async {
    final alerts = await _load();
    final updated = alerts.map((a) => a.copyWith(isRead: true)).toList();
    await _save(updated);
    return fetchAlerts();
  }

  @override
  Future<List<AlertModel>> markRead(String id) async {
    final alerts = await _load();
    final updated =
        alerts.map((a) => a.id == id ? a.copyWith(isRead: true) : a).toList();
    await _save(updated);
    return fetchAlerts();
  }

  @override
  Future<List<AlertModel>> addAlert(AlertModel alert) async {
    final alerts = await _load();

    if (alerts.any((a) => a.id == alert.id)) return fetchAlerts();

    alerts.insert(0, alert);
    await _save(alerts);
    return fetchAlerts();
  }
}

// ─── Global helper ────────────────────────────────────────────────────────────

class AlertsStore {
  AlertsStore._();
  static final AlertsStore instance = AlertsStore._();
  final AlertsLocalDataSourceImpl _ds = AlertsLocalDataSourceImpl();

  Future<void> addInterviewFeedbackAlert({
    required String roleName,
    required String sessionId,
  }) async {
    final alert = AlertModel(
      id: 'interview_$sessionId',
      title: 'Interview Feedback Ready',
      body: 'Your $roleName interview feedback is now available. Tap to view.',
      createdAt: DateTime.now(),
      isRead: false,
      type: AlertType.interview,
      route: '/interview-feedback-detail',
    );
    await _ds.addAlert(alert);
  }

  Future<void> addCareerPlanAlert({
    required String planTitle,
    required String planId,
  }) async {
    final alert = AlertModel(
      id: 'plan_$planId',
      title: 'Career Plan Ready',
      body: 'Your "$planTitle" career plan has been generated. Tap to view.',
      createdAt: DateTime.now(),
      isRead: false,
      type: AlertType.plan,
      route: '/career-build/plans',
    );
    await _ds.addAlert(alert);
  }

  Future<void> addCareerPlanRegeneratedAlert({
    required String planTitle,
    required String planId,
  }) async {
    final alert = AlertModel(
      id: 'plan_regen_${planId}_${DateTime.now().millisecondsSinceEpoch}',
      title: 'Career Plan Updated',
      body: 'Your "$planTitle" career plan has been regenerated successfully.',
      createdAt: DateTime.now(),
      isRead: false,
      type: AlertType.plan,
    );
    await _ds.addAlert(alert);
  }
}
