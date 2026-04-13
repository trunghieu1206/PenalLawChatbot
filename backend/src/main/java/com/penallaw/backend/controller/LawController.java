package com.penallaw.backend.controller;

import com.penallaw.backend.dto.LawDTOs;
import com.penallaw.backend.entity.Law;
import com.penallaw.backend.repository.LawRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.List;

/**
 * Provides read-only access to the laws table for the law-reference sidebar.
 * No authentication required — law text is public reference material.
 *
 * GET /api/laws/{articleNumber}?crimeDate=YYYY-MM-DD
 *
 * articleNumber: bare number (e.g. "249") OR prefixed (e.g. "Điều 249") — both are handled.
 * crimeDate: optional ISO date (YYYY-MM-DD). When provided, returns the law version
 *            effective at that date. Falls back to active versions if not provided or no match.
 */
@RestController
@RequestMapping("/api/laws")
@RequiredArgsConstructor
@Slf4j
public class LawController {

    private final LawRepository lawRepository;

    @GetMapping("/{articleNumber}")
    public ResponseEntity<LawDTOs.LawLookupResponse> getLaw(
            @PathVariable String articleNumber,
            @RequestParam(required = false) String crimeDate
    ) {
        // Normalize: strip "Điều " prefix if present (AI returns "Điều 249", DB stores "249")
        String normalized = articleNumber.trim();
        if (normalized.toLowerCase().startsWith("điều ")) {
            normalized = normalized.substring(5).trim();
        }

        log.debug("Law lookup: article='{}' (normalized='{}'), crimeDate='{}'",
                articleNumber, normalized, crimeDate);

        // Attempt version-aware query if crimeDate provided
        List<Law> laws = List.of();
        String foundBy = "active_fallback";

        if (crimeDate != null && !crimeDate.isBlank()) {
            LocalDate date = parseDate(crimeDate);
            if (date != null) {
                laws = lawRepository.findByArticleNumberAndCrimeDate(normalized, date);
                if (!laws.isEmpty()) {
                    foundBy = "crime_date";
                    log.debug("Found {} version(s) for Điều {} at {}", laws.size(), normalized, date);
                }
            }
        }

        // Fallback: active versions (no date filter)
        if (laws.isEmpty()) {
            laws = lawRepository.findActiveByArticleNumber(normalized);
            log.debug("Fallback: found {} active version(s) for Điều {}", laws.size(), normalized);
        }

        // Last resort: any version at all
        if (laws.isEmpty()) {
            laws = lawRepository.findByArticleNumberOrderByEffectiveDateDesc(normalized);
            log.debug("Last resort: found {} total version(s) for Điều {}", laws.size(), normalized);
        }

        List<LawDTOs.LawResponse> versions = laws.stream()
                .map(l -> new LawDTOs.LawResponse(
                        l.getId(),
                        l.getArticleNumber(),
                        l.getTitle(),
                        l.getChapter(),
                        l.getContent(),
                        l.getSource(),
                        l.getEffectiveDate(),
                        l.getEffectiveEndDate(),
                        l.getIsActive(),
                        l.getVersion()
                ))
                .toList();

        return ResponseEntity.ok(new LawDTOs.LawLookupResponse(
                normalized,
                crimeDate,
                foundBy,
                versions
        ));
    }

    /** Parse YYYY-MM-DD ISO date string. Returns null on failure. */
    private LocalDate parseDate(String dateStr) {
        try {
            return LocalDate.parse(dateStr.trim(), DateTimeFormatter.ISO_LOCAL_DATE);
        } catch (DateTimeParseException e) {
            log.warn("Could not parse crimeDate '{}': {}", dateStr, e.getMessage());
            return null;
        }
    }
}
