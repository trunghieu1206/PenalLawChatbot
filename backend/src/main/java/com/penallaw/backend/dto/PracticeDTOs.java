package com.penallaw.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;

import java.util.List;
import java.util.Map;

/**
 * DTOs for the Practice Mode (Study Mode) evaluation endpoint.
 * The Java backend proxies these straight through to the Python AI Service.
 */
public class PracticeDTOs {

    /** Request body from the React frontend → POST /api/training/evaluate */
    public record EvaluateRequest(
            @NotBlank
            @JsonProperty("case_description") String caseDescription,

            /** One of: "neutral" (judge), "defense", "victim" */
            @JsonProperty("user_mode") String userMode,

            @NotBlank
            @JsonProperty("user_analysis") String userAnalysis
    ) {}

    /** Nested feedback block returned by the AI service. */
    public record Feedback(
            List<String> strengths,
            List<String> improvements,
            String suggestion,
            @JsonProperty("suggested_laws") List<Map<String, String>> suggestedLaws
    ) {}

    /** Response shape from Python AI service → forwarded back to the frontend. */
    public record EvaluateResponse(
            int score,
            Feedback feedback
    ) {}
}
