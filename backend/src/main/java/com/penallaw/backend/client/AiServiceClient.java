package com.penallaw.backend.client;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
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
        return webClientBuilder.baseUrl(aiServiceBaseUrl).build();
    }

    public record PredictRequest(
            @JsonProperty("case_content") String caseContent,
            String role,
            @JsonProperty("rebuttal_against") String rebuttalAgainst,
            @JsonProperty("conversation_history") List<Map<String, String>> conversationHistory
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
     */
    public PredictResponse predict(String caseContent, String role, String rebuttalAgainst,
                                   List<Map<String, String>> conversationHistory) {
        PredictRequest request = new PredictRequest(caseContent, role, rebuttalAgainst, conversationHistory);
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
