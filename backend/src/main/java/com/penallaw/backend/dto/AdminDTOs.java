package com.penallaw.backend.dto;

import com.fasterxml.jackson.annotation.JsonFormat;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public class AdminDTOs {

    /** One message in a conversation (for admin feedback view). */
    public record MessageSummary(
            UUID id,
            String role,
            String content,
            @JsonFormat(pattern = "yyyy-MM-dd'T'HH:mm:ss[.SSS]'Z'", timezone = "UTC")
            LocalDateTime createdAt
    ) {}

    /**
     * Full feedback record returned to the admin panel.
     * Includes the full conversation context and review status.
     */
    public record FeedbackDetail(
            UUID id,
            @JsonProperty("session_id")   UUID    sessionId,
            @JsonProperty("message_id")   UUID    messageId,
            @JsonProperty("is_correct")   Boolean isCorrect,
            String comment,
            /** "can_xem_xet" | "da_xem_xet" */
            String status,
            @JsonFormat(pattern = "yyyy-MM-dd'T'HH:mm:ss[.SSS]'Z'", timezone = "UTC")
            @JsonProperty("created_at")   LocalDateTime createdAt,
            @JsonProperty("session_mode") String sessionMode,
            List<MessageSummary> conversation
    ) {}

    /** Response after submitting feedback. */
    public record FeedbackResponse(UUID id, String message) {}

    /** Request body for feedback submission. */
    public record FeedbackRequest(
            @JsonProperty("session_id") UUID    sessionId,
            @JsonProperty("message_id") UUID    messageId,
            @JsonProperty("is_correct") boolean isCorrect,
            String comment
    ) {}

    /** Request body for PATCH /admin/feedback/{id}/status */
    public record StatusUpdateRequest(String status) {}

    /** Per-user case (session) statistics for the admin user-stats tab. */
    public record UserCaseStat(
            @JsonProperty("user_id")     UUID   userId,
            String                              email,
            @JsonProperty("full_name")   String fullName,
            String                              role,
            @JsonProperty("total_cases") long   totalCases,
            @JsonProperty("cases_today") long   casesToday
    ) {}
}
