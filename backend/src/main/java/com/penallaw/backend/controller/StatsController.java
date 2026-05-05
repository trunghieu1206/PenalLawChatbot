package com.penallaw.backend.controller;

import com.penallaw.backend.dto.AdminDTOs;
import com.penallaw.backend.service.AdminService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

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

    @GetMapping
    public ResponseEntity<AdminDTOs.DashboardStats> getStats() {
        return ResponseEntity.ok(adminService.getStats());
    }
}
