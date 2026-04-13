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
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;

/**
 * Provides read-only access to the laws table for the law-reference sidebar.
 * No authentication required — law text is public reference material.
 *
 * GET /api/laws/{articleNumber}?crimeDate=YYYY-MM-DD
 *
 * articleNumber: bare number (e.g. "249") OR prefixed (e.g. "Điều 249") — both handled.
 *               Law-code suffixes ("BLHS", "BLTTHS", etc.) are automatically stripped.
 * crimeDate: optional ISO date (YYYY-MM-DD). When provided, the version applicable at
 *            that date is placed first in the response (shown as default tab in UI).
 *            ALL other available versions are still returned as additional tabs.
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
        // Step 1: Strip "Điều " prefix if present (AI returns "Điều 249", DB stores "249")
        String normalized = articleNumber.trim();
        if (normalized.toLowerCase().startsWith("điều ")) {
            normalized = normalized.substring(5).trim();
        }

        // Step 2: Strip law-code suffixes like "BLHS", "BLHS 2015", "BLTTHS", etc.
        // Catches patterns like "51 BLHS", "51 BLHS 2015", "249 BLTTHS"
        normalized = normalized.replaceAll("(?i)\\s+(BLHS|BLTTHS|BL[A-Z]+).*$", "").trim();

        log.debug("Law lookup: article='{}' (normalized='{}'), crimeDate='{}'",
                articleNumber, normalized, crimeDate);

        // Step 3: Fetch ALL versions of this article (most recent first)
        List<Law> allVersions = lawRepository.findAllVersionsByArticleNumber(normalized);

        if (allVersions.isEmpty()) {
            log.debug("No versions found for Điều {}", normalized);
            return ResponseEntity.ok(new LawDTOs.LawLookupResponse(
                    normalized, crimeDate, "not_found", List.of()
            ));
        }

        // Step 4: If crimeDate is provided, partition into "applicable at crime date" (first)
        //         and "other versions" (remaining tabs), so the correct historical law is
        //         the default tab while all other versions remain accessible.
        String foundBy = "active_fallback";
        List<Law> sorted;

        if (crimeDate != null && !crimeDate.isBlank()) {
            LocalDate date = parseDate(crimeDate);
            if (date != null) {
                List<Law> applicable = allVersions.stream()
                        .filter(l -> (l.getEffectiveDate() == null || !l.getEffectiveDate().isAfter(date))
                                  && (l.getEffectiveEndDate() == null || !l.getEffectiveEndDate().isBefore(date)))
                        .collect(Collectors.toList());

                List<Law> others = allVersions.stream()
                        .filter(l -> !applicable.contains(l))
                        .collect(Collectors.toList());

                if (!applicable.isEmpty()) {
                    foundBy = "crime_date";
                    log.debug("Found {} version(s) applicable at {} for Điều {}; {} other version(s) also returned",
                            applicable.size(), date, normalized, others.size());
                } else {
                    log.debug("No version applicable at {} for Điều {}; returning all {} version(s)",
                            date, normalized, allVersions.size());
                }

                sorted = new ArrayList<>();
                sorted.addAll(applicable);   // default tab = crime-date applicable
                sorted.addAll(others);        // remaining tabs = other historical versions
            } else {
                sorted = allVersions; // date parse failed; fall through to most-recent-first
            }
        } else {
            sorted = allVersions; // no crime date → most recent version is first tab
        }

        List<LawDTOs.LawResponse> versions = sorted.stream()
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
