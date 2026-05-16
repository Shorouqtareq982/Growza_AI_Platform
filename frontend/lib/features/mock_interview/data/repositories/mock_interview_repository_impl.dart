import 'dart:convert';
import 'dart:io';

import 'package:dio/dio.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:supabase_flutter/supabase_flutter.dart';
import 'package:flutter/foundation.dart';

import '../../../../core/network/api_client.dart';
import '../../domain/entities/interview_entities.dart';
import '../models/interview_models.dart';

class MockInterviewRepositoryImpl {
  final Dio _dio = apiClient.dio;

  static const String _base = '/api/v1/mock-interview';
  static const String _cacheKey = 'mock_interview_sessions';

  // ─── Start Behavioral Session ──────────────────────────────────────
  // POST /api/v1/mock-interview/sessions/start/behavioral
  // Body: { role_name, user_id }
  // Returns: { session_id, questions, sas_token, blob_url, sas_expires_at }

  Future<InterviewSessionEntity> startBehavioralSession({
    required String roleName,
    required String userId,
  }) async {
    final response = await _dio.post(
      '$_base/sessions/start/behavioral',
      data: {
        'role_name': roleName,
        'user_id': userId,
      },
    );
    return InterviewSessionModel.fromJson(
      response.data as Map<String, dynamic>,
    ).toEntity(InterviewSessionType.behavioral);
  }

  // ─── Start Technical Session  ───────────────────────────────────
  // POST /api/v1/mock-interview/sessions/start/technical
  // Body: { role_name, user_id }
  // Returns: { session_id, questions, sas_token, blob_url, sas_expires_at }

  Future<InterviewSessionEntity> startTechnicalSession({
    required String roleName,
    required String userId,
  }) async {
    final response = await _dio.post(
      '$_base/sessions/start/technical',
      data: {
        'role_name': roleName,
        'user_id': userId,
      },
    );
    return InterviewSessionModel.fromJson(
      response.data as Map<String, dynamic>,
    ).toEntity(InterviewSessionType.technical);
  }

  // ─── Get Question Audio ────────────────────────────────────────────────────
  // GET /api/v1/mock-interview/questions/{question_id}/audio-stream
  // Returns: audio/mpeg bytes stream

  Future<List<int>> getQuestionAudio(String questionId) async {
    final response = await _dio.get<List<int>>(
      '$_base/questions/$questionId/audio-stream',
      options: Options(responseType: ResponseType.bytes),
    );
    return response.data ?? [];
  }

  // ─── Notify Upload Complete ────────────────────────────────────────────────
  // POST /api/v1/mock-interview/notify-upload
  // Body: { session_id, blob_url }
  // Works for both behavioral AND technical sessions

  Future<void> notifyUploadComplete({
    required String sessionId,
    required String blobUrl,
  }) async {
    await _dio.post(
      '$_base/notify-upload',
      data: {
        'session_id': sessionId,
        'blob_url': blobUrl,
      },
    );
  }

  // ─── Get Behavioral Report ─────────────────────────────────────────────────
  // GET /api/v1/mock-interview/analysis/{session_id}/behavioral-report
  // Returns: String (markdown text)

  Future<String> getBehavioralReport(String sessionId) async {
    final response = await _dio.get<dynamic>(
      '$_base/analysis/$sessionId/behavioral-report',
    );
    return response.data?.toString() ?? '';
  }

  // ─── Get Technical Report ──────────────────────────────────────────────────
  // GET /api/v1/mock-interview/analysis/{session_id}/technical-report
  // Returns: String (markdown text)

  Future<String> getTechnicalReport(String sessionId) async {
    final response = await _dio.get<dynamic>(
      '$_base/analysis/$sessionId/technical-report',
    );
    return response.data?.toString() ?? '';
  }

  // ─── Upload to Azure Blob ──────────────────────────────────────────────────
  // الباك بيبعت blob_url كامل + sas_token
  // إحنا بنعمل PUT على الـ blob_url مباشرة مع الـ sas_token في الـ URL
  // الفرق: behavioral → video/mp4 | technical → audio/mp3

  Future<void> uploadToAzure({
    required File file,
    required String blobUrl,
    required String sasToken,
    required InterviewSessionType sessionType,
  }) async {
    final isVideo = sessionType == InterviewSessionType.behavioral;
    final contentType = isVideo ? 'video/mp4' : 'audio/mpeg';

    // Build upload URL: blob_url?sas_token
    final uploadUrl =
        blobUrl.contains('?') ? '$blobUrl&$sasToken' : '$blobUrl?$sasToken';

    final fileBytes = await file.readAsBytes();

    final azureDio = Dio();
    await azureDio.put(
      uploadUrl,
      data: Stream.fromIterable(fileBytes.map((e) => [e])),
      options: Options(
        headers: {
          'x-ms-blob-type': 'BlockBlob',
          'Content-Type': contentType,
          'Content-Length': fileBytes.length,
        },
        sendTimeout: const Duration(minutes: 5),
        receiveTimeout: const Duration(minutes: 2),
      ),
    );
  }

  // ─── Local Cache for Sessions ──────────────────────────────────────────────

  Future<void> saveSessionLocally({
    required String sessionId,
    required String roleName,
    required InterviewSessionType sessionType,
  }) async {
    final userId = Supabase.instance.client.auth.currentUser?.id;

    if (userId != null) {
      try {
        await Supabase.instance.client.from('interview_sessions').upsert({
          'session_id': sessionId,
          'user_id': userId,
          'role_name': roleName,
          'session_type': sessionType.name,
          'created_at': DateTime.now().toIso8601String(),
        });
      } catch (e) {
        debugPrint('Supabase session save failed: $e');
      }
    }

    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_cacheKey);
    final List<Map<String, dynamic>> sessions = raw != null
        ? List<Map<String, dynamic>>.from(jsonDecode(raw) as List)
        : [];
    sessions.removeWhere((s) => s['session_id'] == sessionId);
    sessions.insert(0, {
      'session_id': sessionId,
      'role_name': roleName,
      'session_type': sessionType.name,
      'created_at': DateTime.now().toIso8601String(),
    });
    await prefs.setString(_cacheKey, jsonEncode(sessions));
  }

  Future<List<Map<String, dynamic>>> getLocalSessions() async {
    final userId = Supabase.instance.client.auth.currentUser?.id;

    if (userId != null) {
      try {
        final response = await Supabase.instance.client
            .from('interview_sessions')
            .select()
            .eq('user_id', userId)
            .order('created_at', ascending: false);

        final sessions = List<Map<String, dynamic>>.from(response as List);

        final prefs = await SharedPreferences.getInstance();
        await prefs.setString(_cacheKey, jsonEncode(sessions));

        return sessions;
      } catch (e) {
        debugPrint('Supabase fetch failed, falling back to cache: $e');
      }
    }

    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_cacheKey);
    if (raw == null) return [];
    return List<Map<String, dynamic>>.from(jsonDecode(raw) as List);
  }

  Future<void> deleteLocalSession(String sessionId) async {
    try {
      await Supabase.instance.client
          .from('interview_sessions')
          .delete()
          .eq('session_id', sessionId);
    } catch (e) {
      debugPrint('Supabase delete failed: $e');
    }

    final prefs = await SharedPreferences.getInstance();
    final raw = prefs.getString(_cacheKey);
    if (raw == null) return;
    final sessions = List<Map<String, dynamic>>.from(jsonDecode(raw) as List);
    sessions.removeWhere((s) => s['session_id'] == sessionId);
    await prefs.setString(_cacheKey, jsonEncode(sessions));
  }
}
