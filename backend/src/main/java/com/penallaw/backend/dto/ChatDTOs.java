package com.penallaw.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public class ChatDTOs {

    public record CreateSessionRequest(
            String mode  // "defense", "victim", "neutral"
    ) {}

    public record SessionResponse(
            UUID id,
            String mode,
            LocalDateTime createdAt
    ) {}

    public record SendMessageRequest(
            @NotBlank String content,
            String role,  // optional override
            @JsonProperty("rebuttal_against") String rebuttalAgainst
    ) {}

    public record MessageResponse(
            UUID id,
            String role,
            String content,
            @JsonProperty("extracted_facts") Map<String, Object> extractedFacts,
            @JsonProperty("mapped_laws") List<Map<String, String>> mappedLaws,
            LocalDateTime createdAt
    ) {}

    public record ConversationHistoryResponse(
            UUID sessionId,
            String mode,
            List<MessageResponse> messages
    ) {}
}
