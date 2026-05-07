package com.penallaw.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Map;

/**
 * DTOs used exclusively by the public /api/home stats endpoints.
 * Separated from AdminDTOs to cleanly decouple public and admin contracts.
 */
public class StatsDTOs {

    /**
     * Aggregate dashboard statistics visible to any authenticated user.
     * Mirrors the data shown on the public Stats page.
     */
    public record DashboardStats(
            @JsonProperty("total_sessions")     long totalSessions,
            @JsonProperty("total_users")        long totalUsers,
            @JsonProperty("cases_processed")    long casesProcessed,
            @JsonProperty("visitor_count")      long visitorCount,
            @JsonProperty("by_role")            Map<String, Long> byRole,
            @JsonProperty("by_province")        Map<String, Long> byProvince,
            @JsonProperty("by_crime_type")      Map<String, Long> byCrimeType,
            @JsonProperty("feedback_total")     long feedbackTotal,
            @JsonProperty("feedback_correct")   long feedbackCorrect,
            @JsonProperty("feedback_incorrect") long feedbackIncorrect
    ) {}
}
