package com.penallaw.backend.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

import java.time.LocalDate;
import java.util.List;

public class LawDTOs {

    /**
     * Single law version returned to the frontend.
     */
    public record LawResponse(
            Integer id,
            @JsonProperty("article_number") String articleNumber,
            String title,
            String chapter,
            String content,
            String source,
            @JsonProperty("effective_date") LocalDate effectiveDate,
            @JsonProperty("effective_end_date") LocalDate effectiveEndDate,
            @JsonProperty("is_active") Boolean isActive,
            Integer version
    ) {}

    /**
     * All matching versions for a given article number + crime date.
     * The frontend should display versions[0] as the primary applicable version.
     */
    public record LawLookupResponse(
            @JsonProperty("article_number") String articleNumber,
            @JsonProperty("crime_date") String crimeDate,       // echoed back so frontend can display
            @JsonProperty("found_by") String foundBy,           // "crime_date" or "active_fallback"
            List<LawResponse> versions
    ) {}
}
