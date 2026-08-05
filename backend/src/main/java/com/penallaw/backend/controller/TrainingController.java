package com.penallaw.backend.controller;

import com.penallaw.backend.client.AiServiceClient;
import com.penallaw.backend.dto.PracticeDTOs;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/**
 * REST controller for Practice (Study) Mode.
 *
 * All requests are authenticated via Spring Security (JWT) before reaching here.
 * The controller is a thin proxy — it forwards the request to the Python AI
 * service via AiServiceClient and returns the evaluation result directly.
 *
 * Previously, the React frontend bypassed this backend and called the Python
 * AI service directly via the /ai-api/ nginx proxy (no authentication, no rate
 * limiting). Routing through this controller closes that security gap.
 */
@RestController
@RequestMapping("/api/training")
@RequiredArgsConstructor
@Slf4j
public class TrainingController {

    private final AiServiceClient aiServiceClient;

    /**
     * Evaluate a user's legal analysis for Practice Mode.
     *
     * POST /api/training/evaluate
     * Requires: valid JWT token (any authenticated user, not just ADMIN).
     */
    @PostMapping("/evaluate")
    public ResponseEntity<PracticeDTOs.EvaluateResponse> evaluate(
            @Valid @RequestBody PracticeDTOs.EvaluateRequest request
    ) {
        log.info("[TRAINING] Evaluating practice submission | mode={} | case_len={} | analysis_len={}",
                request.userMode(),
                request.caseDescription() != null ? request.caseDescription().length() : 0,
                request.userAnalysis() != null ? request.userAnalysis().length() : 0);

        PracticeDTOs.EvaluateResponse result = aiServiceClient.evaluatePractice(request);
        return ResponseEntity.ok(result);
    }
}
