package com.penallaw.backend.client;

import com.fasterxml.jackson.annotation.JsonProperty;
import com.penallaw.backend.dto.PracticeDTOs;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.List;
import java.util.Map;

@Service
@RequiredArgsConstructor
public class AiServiceClient {

    @Value("${ai-service.base-url}")
    private String aiServiceBaseUrl;

    @Value("${ai-service.timeout-seconds}")
    private int timeoutSeconds;

    private final WebClient.Builder webClientBuilder;

    /**
     * Single shared WebClient instance built once at startup.
     * WebClient is thread-safe and designed to be reused across requests.
     * Building it on every call (as before) was wasteful.
     */
    private WebClient client;

    @PostConstruct
    private void init() {
        // Default WebClient buffer is 256KB — too small for /predict responses
        // (full Vietnamese legal analysis + mapped_laws + extracted_facts can exceed 1MB).
        // Increase to 10MB to safely handle all response sizes.
        ExchangeStrategies strategies = ExchangeStrategies.builder()
                .codecs(config -> config.defaultCodecs().maxInMemorySize(10 * 1024 * 1024))
                .build();
        this.client = webClientBuilder
                .baseUrl(aiServiceBaseUrl)
                .exchangeStrategies(strategies)
                .build();
    }

    public record PredictRequest(
            @JsonProperty("case_content") String caseContent,
            String role,
            // Map<String, Object> allows assistant entries to carry mapped_laws (nested structure)
            // alongside the plain String fields "role" and "content".
            @JsonProperty("conversation_history") List<Map<String, Object>> conversationHistory,
            // session_id is used as thread_id for LangGraph MemorySaver checkpointing.
            // On follow-up turns, the Python service restores the full AgentState
            // (documents, mapped_laws, extracted_facts) from this checkpoint.
            @JsonProperty("session_id") String sessionId
    ) {}

    public record PredictResponse(
            String result,
            @JsonProperty("extracted_facts") Map<String, Object> extractedFacts,
            @JsonProperty("mapped_laws") List<Map<String, Object>> mappedLaws,
            @JsonProperty("sentencing_data") Map<String, Object> sentencingData
    ) {}

    /**
     * Call Python AI service to get legal analysis.
     * @param conversationHistory prior messages as [{role, content}] for context
     * @param sessionId session UUID used as thread_id for LangGraph checkpointing
     */
    public PredictResponse predict(String caseContent, String role,
                                   List<Map<String, Object>> conversationHistory, String sessionId) {
        PredictRequest request = new PredictRequest(caseContent, role, conversationHistory, sessionId);
        return client.post()
                .uri("/predict")
                .bodyValue(request)
                .retrieve()
                .bodyToMono(PredictResponse.class)
                .timeout(Duration.ofSeconds(timeoutSeconds))
                .onErrorMap(ex -> new RuntimeException("AI service error: " + ex.getMessage(), ex))
                .block();
    }

    /**
     * Forward a Practice Mode evaluation request to the Python AI service.
     * Mirrors the shape of PracticeEvalRequest / PracticeEvalResponse in main.py.
     */
    public PracticeDTOs.EvaluateResponse evaluatePractice(PracticeDTOs.EvaluateRequest request) {
        return client.post()
                .uri("/practice/evaluate")
                .bodyValue(request)
                .retrieve()
                .bodyToMono(PracticeDTOs.EvaluateResponse.class)
                .timeout(Duration.ofSeconds(timeoutSeconds))
                .onErrorMap(ex -> new RuntimeException("AI service error (practice): " + ex.getMessage(), ex))
                .block();
    }
}
