package com.penallaw.backend.controller;

import com.penallaw.backend.dto.AdminDTOs;
import com.penallaw.backend.service.AdminService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.UUID;

/**
 * Admin endpoints — secured at the URL level by SecurityConfig (ROLE_ADMIN only).
 *
 * GET   /api/admin/feedback          — all feedback with full session conversations
 * POST  /api/admin/feedback          — submit feedback on an AI response (open to all users)
 * PATCH /api/admin/feedback/{id}/status — update the review status of a feedback record
 * GET   /api/admin/user-stats        — per-user session/case counts
 *
 * Public aggregate statistics (total sessions, visitors, etc.) have been moved to
 * StatsController at /api/home (accessible to all authenticated users).
 */
@RestController
@RequestMapping("/api/admin")
@RequiredArgsConstructor
public class AdminController {

    private final AdminService adminService;

    /** All feedback records with full conversation context (admin view). */
    @GetMapping("/feedback")
    public ResponseEntity<List<AdminDTOs.FeedbackDetail>> getAllFeedback() {
        return ResponseEntity.ok(adminService.getAllFeedback());
    }

    /** Per-user session (case) counts for the admin user-stats tab. */
    @GetMapping("/user-stats")
    public ResponseEntity<List<AdminDTOs.UserCaseStat>> getUserCaseStats() {
        return ResponseEntity.ok(adminService.getUserCaseStats());
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
        if (request.messageId() == null) {
            return ResponseEntity.badRequest().build();
        }
        AdminDTOs.FeedbackResponse resp = adminService.submitFeedback(
                request.sessionId(),
                request.messageId(),
                request.isCorrect(),
                request.comment()
        );
        return ResponseEntity.ok(resp);
    }

    /**
     * Update the review status of a feedback record.
     * PATCH /api/admin/feedback/{id}/status
     * Body: { "status": "da_xem_xet" | "can_xem_xet" }
     */
    @PatchMapping("/feedback/{id}/status")
    public ResponseEntity<AdminDTOs.FeedbackResponse> updateFeedbackStatus(
            @PathVariable UUID id,
            @RequestBody AdminDTOs.StatusUpdateRequest request
    ) {
        return ResponseEntity.ok(adminService.updateFeedbackStatus(id, request.status()));
    }
}
