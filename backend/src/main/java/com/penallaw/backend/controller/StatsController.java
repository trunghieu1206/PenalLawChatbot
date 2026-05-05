package com.penallaw.backend.controller;

import com.penallaw.backend.dto.AdminDTOs;
import com.penallaw.backend.service.AdminService;
import com.penallaw.backend.service.VisitorTrackingService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

/**
 * Public statistics endpoint — accessible to any user (no authentication required).
 * GET /api/home  →  aggregate dashboard statistics (sessions, cases, by province, by role, etc.)
 *
 * Separate from /api/admin/** (which is ROLE_ADMIN only) so that ordinary users
 * can view system-wide metrics without needing admin credentials.
 */
@RestController
@RequestMapping("/api/home")
@RequiredArgsConstructor
public class StatsController {

    private final AdminService adminService;
    private final VisitorTrackingService visitorTrackingService;

    @GetMapping
    public ResponseEntity<AdminDTOs.DashboardStats> getStats() {
        return ResponseEntity.ok(adminService.getStats());
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
