package com.penallaw.backend.service;

import com.penallaw.backend.dto.LawDTOs;
import com.penallaw.backend.entity.Law;
import com.penallaw.backend.repository.LawRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;

/**
 * Business logic for looking up cited laws.
 *
 * Responsibilities:
 *  - Normalize raw article numbers (strip "Điều " prefix and law-code suffixes)
 *  - Fetch matching law versions from the database
 *  - Partition and order versions by crime date when provided
 *  - Map Law entities to LawResponse DTOs
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class LawService {

    private static final String DIEU_PREFIX = "điều ";
    private static final String LAW_CODE_SUFFIX_PATTERN = "(?i)\\s+(BLHS|BLTTHS|BL[A-Z]+).*$";

    private final LawRepository lawRepository;

    /**
     * Look up all applicable versions of an article by number, optionally
     * filtered by source and ordered by crime date.
     *
     * @param rawArticleNumber  raw article number as received from the client
     *                          (may contain "Điều " prefix or law-code suffixes)
     * @param crimeDate         optional ISO date string (YYYY-MM-DD); when provided,
     *                          the historically applicable version is placed first
     * @param source            optional source filter (e.g. "Bộ luật Hình sự 2025");
     *                          when provided, returns only the article from that source
     * @return a {@link LawDTOs.LawLookupResponse} containing all matching versions
     */
    @Transactional(readOnly = true)
    public LawDTOs.LawLookupResponse lookupLaw(String rawArticleNumber, String crimeDate, String source) {
        String normalized = normalizeArticleNumber(rawArticleNumber);

        log.debug("Law lookup: article='{}' (normalized='{}'), source='{}', crimeDate='{}'",
                rawArticleNumber, normalized, source, crimeDate);

        List<Law> allVersions = fetchVersions(normalized, source);

        if (allVersions.isEmpty()) {
            log.debug("No versions found for Điều {}", normalized);
            return new LawDTOs.LawLookupResponse(normalized, crimeDate, "not_found", List.of());
        }

        String foundBy = "active_fallback";
        List<Law> sorted;

        if (crimeDate != null && !crimeDate.isBlank()) {
            LocalDate date = parseDate(crimeDate);
            if (date != null) {
                sorted = new ArrayList<>();
                List<Law> applicable = filterApplicableAtDate(allVersions, date);
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

                sorted.addAll(applicable); // default tab = crime-date applicable
                sorted.addAll(others);     // remaining tabs = other historical versions
            } else {
                sorted = allVersions; // date parse failed; fall through to most-recent-first
            }
        } else {
            sorted = allVersions; // no crime date → most recent version is first tab
        }

        List<LawDTOs.LawResponse> versions = toResponseList(sorted);
        return new LawDTOs.LawLookupResponse(normalized, crimeDate, foundBy, versions);
    }

    // ── Private helpers ──────────────────────────────────────────────────────

    /**
     * Normalize an article number by stripping the Vietnamese "Điều " prefix
     * and any trailing law-code suffix (e.g. "BLHS 2015", "BLTTHS").
     */
    private String normalizeArticleNumber(String raw) {
        String normalized = raw.trim();
        if (normalized.toLowerCase().startsWith(DIEU_PREFIX)) {
            normalized = normalized.substring(DIEU_PREFIX.length()).trim();
        }
        return normalized.replaceAll(LAW_CODE_SUFFIX_PATTERN, "").trim();
    }

    /**
     * Fetch law versions from the repository.
     * When a source is provided, returns only the single version matching that source.
     * Otherwise, returns all versions ordered by effectiveDate DESC.
     */
    private List<Law> fetchVersions(String normalizedArticleNumber, String source) {
        if (source != null && !source.isBlank()) {
            Law specificVersion = lawRepository.findByArticleNumberAndSource(normalizedArticleNumber, source);
            return specificVersion != null ? List.of(specificVersion) : List.of();
        }
        return lawRepository.findAllVersionsByArticleNumber(normalizedArticleNumber);
    }

    /**
     * Filter laws that were in effect on the given date.
     * A law is applicable if its effectiveDate is on or before the date
     * AND its effectiveEndDate is null (still active) or on or after the date.
     */
    private List<Law> filterApplicableAtDate(List<Law> laws, LocalDate date) {
        return laws.stream()
                .filter(l ->
                        (l.getEffectiveDate() == null || !l.getEffectiveDate().isAfter(date))
                        && (l.getEffectiveEndDate() == null || !l.getEffectiveEndDate().isBefore(date))
                )
                .collect(Collectors.toList());
    }

    /**
     * Map a list of {@link Law} entities to {@link LawDTOs.LawResponse} DTOs.
     */
    private List<LawDTOs.LawResponse> toResponseList(List<Law> laws) {
        return laws.stream()
                .map(l -> new LawDTOs.LawResponse(
                        l.getId(),
                        l.getArticleNumber(),
                        l.getTitle(),
                        l.getChapter(),
                        l.getContent(),
                        l.getSource(),
                        l.getEffectiveDate(),
                        l.getEffectiveEndDate(),
                        l.getIsActive()
                ))
                .toList();
    }

    /**
     * Parse an ISO date string (YYYY-MM-DD).
     * Returns {@code null} on failure so the caller can gracefully fall back.
     */
    private LocalDate parseDate(String dateStr) {
        try {
            return LocalDate.parse(dateStr.trim(), DateTimeFormatter.ISO_LOCAL_DATE);
        } catch (DateTimeParseException e) {
            log.warn("Could not parse crimeDate '{}': {}", dateStr, e.getMessage());
            return null;
        }
    }
}
