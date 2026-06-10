package com.penallaw.backend.client;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.ExchangeStrategies;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

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

    private WebClient getClient() {
        // Default WebClient buffer is 256KB — too small for /predict responses
        // (full Vietnamese legal analysis + mapped_laws + extracted_facts can exceed 1MB).
        // Increase to 10MB to safely handle all response sizes.
        ExchangeStrategies strategies = ExchangeStrategies.builder()
                .codecs(config -> config.defaultCodecs().maxInMemorySize(10 * 1024 * 1024))
                .build();
        return webClientBuilder
                .baseUrl(aiServiceBaseUrl)
                .exchangeStrategies(strategies)
                .build();
    }

    public record PredictRequest(
            @JsonProperty("case_content") String caseContent,
            String role,
            @JsonProperty("rebuttal_against") String rebuttalAgainst,
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

    public record HealthResponse(String status, String device, @JsonProperty("model_loaded") boolean modelLoaded) {}

    /**
     * Call Python AI service to get legal analysis.
     * @param conversationHistory prior messages as [{role, content}] for context
     * @param sessionId session UUID used as thread_id for LangGraph checkpointing
     */
    public PredictResponse predict(String caseContent, String role, String rebuttalAgainst,
                                   List<Map<String, Object>> conversationHistory, String sessionId) {
        PredictRequest request = new PredictRequest(caseContent, role, rebuttalAgainst, conversationHistory, sessionId);
        return getClient().post()
                .uri("/predict")
                .bodyValue(request)
                .retrieve()
                .bodyToMono(PredictResponse.class)
                .timeout(Duration.ofSeconds(timeoutSeconds))
                .onErrorMap(ex -> new RuntimeException("AI service error: " + ex.getMessage(), ex))
                .block();
    }

    /**
     * Check AI service health.
     */
    public Mono<HealthResponse> checkHealth() {
        return getClient().get()
                .uri("/health")
                .retrieve()
                .bodyToMono(HealthResponse.class)
                .timeout(Duration.ofSeconds(5));
    }
}
