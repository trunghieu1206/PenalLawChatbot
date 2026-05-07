package com.penallaw.backend.controller;

import com.penallaw.backend.dto.StatsDTOs;
import com.penallaw.backend.service.StatsService;
import com.penallaw.backend.service.VisitorTrackingService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * Public statistics endpoints — accessible to any authenticated user (not admin-only).
 *
 * GET  /api/home              — aggregate dashboard statistics
 * POST /api/home/track-visit  — record a unique daily browser visit
 *
 * Completely separated from /api/admin/** (ROLE_ADMIN only), ensuring
 * ordinary users can view system-wide metrics without admin credentials.
 */
@RestController
@RequestMapping("/api/home")
@RequiredArgsConstructor
public class StatsController {

    private final StatsService statsService;
    private final VisitorTrackingService visitorTrackingService;

    /** Return aggregate dashboard statistics for the public Stats page. */
    @GetMapping
    public ResponseEntity<StatsDTOs.DashboardStats> getStats() {
        return ResponseEntity.ok(statsService.getStats());
    }

    /**
     * Record a unique daily visit from a browser.
     * Called by the frontend once per day per device (enforced client-side via localStorage).
     * The backend enforces uniqueness via the DB unique constraint on (visitor_id, visit_date).
     *
     * Body: { "visitor_id": "<persistent-uuid-from-browser>" }
     */
    @PostMapping("/track-visit")
    public ResponseEntity<Void> trackVisit(@RequestBody Map<String, String> body) {
        String visitorId = body.get("visitor_id");
        visitorTrackingService.trackVisit(visitorId);
        return ResponseEntity.ok().build();
    }
}
