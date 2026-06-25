package com.penallaw.backend.controller;

import com.penallaw.backend.dto.LawDTOs;
import com.penallaw.backend.service.LawService;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

/**
 * Provides read-only access to the laws table for the law-reference sidebar.
 * No authentication required — law text is public reference material.
 *
 * GET /api/laws/{articleNumber}?crimeDate=YYYY-MM-DD&source=Bộ%20luật%20Hình%20sự%202025
 *
 * articleNumber: bare number (e.g. "249") OR prefixed (e.g. "Điều 249") — both handled.
 *               Law-code suffixes are automatically stripped.
 * crimeDate: optional ISO date (YYYY-MM-DD). When provided, the version applicable at
 *            that date is placed first in the response (shown as default tab in UI).
 *            ALL other available versions are still returned as additional tabs.
 * source: optional source filter (e.g. "Bộ luật Hình sự 2025"). When provided, returns ONLY the
 *         article from that specific source, enabling disambiguation for articles that
 *         have different content across different legal code versions.
 */
@RestController
@RequestMapping("/api/laws")
@RequiredArgsConstructor
public class LawController {

    private final LawService lawService;

    @GetMapping("/{articleNumber}")
    public ResponseEntity<LawDTOs.LawLookupResponse> getLaw(
            @PathVariable String articleNumber,
            @RequestParam(required = false) String crimeDate,
            @RequestParam(required = false) String source
    ) {
        return ResponseEntity.ok(lawService.lookupLaw(articleNumber, crimeDate, source));
    }
}
