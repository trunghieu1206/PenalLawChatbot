package com.penallaw.backend.controller;

import com.penallaw.backend.dto.AdminDTOs;
import com.penallaw.backend.service.AdminService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.UUID;

/**
 * Admin endpoints — no authentication guard for now (thesis scope).
 * Provides dashboard statistics and full feedback history for admin review.
 *
 * GET  /api/admin/stats         — aggregate dashboard statistics
 * GET  /api/admin/feedback      — all feedback with full session conversations
 * POST /api/admin/feedback      — submit feedback on an AI response (called by users)
 */
@RestController
@RequestMapping("/api/admin")
@RequiredArgsConstructor
public class AdminController {

    private final AdminService adminService;

    /** Aggregate dashboard statistics. */
    @GetMapping("/stats")
    public ResponseEntity<AdminDTOs.DashboardStats> getStats() {
        return ResponseEntity.ok(adminService.getStats());
    }

    /** All feedback records with full conversation context (admin view). */
    @GetMapping("/feedback")
    public ResponseEntity<List<AdminDTOs.FeedbackDetail>> getAllFeedback() {
        return ResponseEntity.ok(adminService.getAllFeedback());
    }

    /**
     * Submit feedback on an AI response.
     * Called by ordinary users — POST /api/admin/feedback
     * Body: { session_id, message_id, is_correct, comment? }
     */
    @PostMapping("/feedback")
    public ResponseEntity<AdminDTOs.FeedbackResponse> submitFeedback(
            @RequestBody AdminDTOs.FeedbackRequest request
    ) {
        // session_id is required for conversation linking; message_id targets the specific AI turn
        if (request.messageId() == null) {
            return ResponseEntity.badRequest().build();
        }
        // We derive session from the message — accept session_id directly in a richer DTO later
        // For now, session_id is embedded in FeedbackRequest as sessionId()
        AdminDTOs.FeedbackResponse resp = adminService.submitFeedback(
                request.sessionId(),
                request.messageId(),
                request.isCorrect(),
                request.comment()
        );
        return ResponseEntity.ok(resp);
    }
}
