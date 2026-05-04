package com.penallaw.backend.dto;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public class AdminDTOs {

    /** Aggregate dashboard statistics. */
    public record DashboardStats(
            @JsonProperty("total_sessions")  long totalSessions,
            @JsonProperty("total_users")     long totalUsers,
            @JsonProperty("cases_processed") long casesProcessed,
            @JsonProperty("by_role")         Map<String, Long> byRole,
            @JsonProperty("by_province")     Map<String, Long> byProvince,
            @JsonProperty("by_crime_type")   Map<String, Long> byCrimeType,
            @JsonProperty("feedback_total")    long feedbackTotal,
            @JsonProperty("feedback_correct")  long feedbackCorrect,
            @JsonProperty("feedback_incorrect") long feedbackIncorrect
    ) {}

    /** One message in a conversation (for admin feedback view). */
    public record MessageSummary(
            UUID id,
            String role,
            String content,
            @JsonFormat(pattern = "yyyy-MM-dd'T'HH:mm:ss[.SSS]'Z'", timezone = "UTC")
            LocalDateTime createdAt
    ) {}

    /** Full feedback record, including the conversation context for admin review. */
    public record FeedbackDetail(
            UUID id,
            @JsonProperty("session_id")  UUID sessionId,
            @JsonProperty("message_id")  UUID messageId,
            @JsonProperty("is_correct")  Boolean isCorrect,
            String comment,
            @JsonFormat(pattern = "yyyy-MM-dd'T'HH:mm:ss[.SSS]'Z'", timezone = "UTC")
            @JsonProperty("created_at") LocalDateTime createdAt,
            @JsonProperty("session_mode") String sessionMode,
            List<MessageSummary> conversation
    ) {}

    /** Response after submitting feedback. */
    public record FeedbackResponse(UUID id, String message) {}

    /** Request body for feedback submission. */
    public record FeedbackRequest(
            @JsonProperty("session_id") UUID sessionId,
            @JsonProperty("message_id") UUID messageId,
            @JsonProperty("is_correct") boolean isCorrect,
            String comment
    ) {}
}
