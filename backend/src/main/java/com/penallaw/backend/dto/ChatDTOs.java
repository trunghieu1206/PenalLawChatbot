package com.penallaw.backend.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import com.fasterxml.jackson.annotation.JsonProperty;
import jakarta.validation.constraints.NotBlank;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;

public class ChatDTOs {

    public record CreateSessionRequest(
            String mode,    // "defense", "victim", "neutral"
            String guestId  // nullable — for anonymous guest sessions
    ) {}

    public record SessionResponse(
            UUID id,
            String mode,
            String title,
            LocalDateTime createdAt
    ) {}

    // @JsonAlias allows accepting both "rebuttalAgainst" AND "rebuttal_against" from JSON
    public record SendMessageRequest(
            @NotBlank String content,
            String role,  // optional override
            @JsonAlias("rebuttal_against") String rebuttalAgainst
    ) {}


    public record MessageResponse(
            UUID id,
            String role,
            String content,
            @JsonProperty("extracted_facts") Map<String, Object> extractedFacts,
            @JsonProperty("mapped_laws") List<Map<String, Object>> mappedLaws,
            LocalDateTime createdAt
    ) {}

    public record ConversationHistoryResponse(
            UUID sessionId,
            String mode,
            String title,
            List<MessageResponse> messages
    ) {}

    public record DeleteSessionResponse(UUID id, String message) {}
}
