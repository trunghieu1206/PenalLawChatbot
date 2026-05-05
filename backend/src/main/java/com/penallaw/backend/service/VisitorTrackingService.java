package com.penallaw.backend.service;

import com.penallaw.backend.entity.DailyVisit;
import com.penallaw.backend.repository.DailyVisitRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;

@Service
@RequiredArgsConstructor
@Slf4j
public class VisitorTrackingService {

    private final DailyVisitRepository dailyVisitRepository;

    /**
     * Record a unique daily visit.
     *
     * Logic:
     *   - visitorId is a UUID generated once by the browser and stored in localStorage.
     *   - We record at most ONE row per (visitorId, today) pair.
     *   - If the pair already exists → no-op (idempotent).
     *   - The DB unique constraint (uq_daily_visits_visitor_date) is the final safety net.
     *
     * This means:
     *   - Same user browsing 10 pages in one day  → counted ONCE
     *   - Same user returning the next day        → counted ONCE more
     *   - A completely new browser/device         → counted ONCE
     *
     * @param visitorId UUID string from the browser's localStorage
     */
    @Transactional
    public void trackVisit(String visitorId) {
        if (visitorId == null || visitorId.isBlank()) return;

        LocalDate today = LocalDate.now();
        if (dailyVisitRepository.existsByVisitorIdAndVisitDate(visitorId, today)) {
            return; // Already counted today — no-op
        }

        try {
            dailyVisitRepository.save(DailyVisit.builder()
                    .visitorId(visitorId)
                    .visitDate(today)
                    .build());
            log.debug("New daily visit recorded: visitorId={} date={}", visitorId, today);
        } catch (Exception e) {
            // Race condition: two concurrent first-visits from the same browser.
            // The DB unique constraint prevents a duplicate row — safely ignore.
            log.debug("Duplicate visit insert ignored (race condition): {}", e.getMessage());
        }
    }

    /**
     * Returns the total number of unique daily visit events ever recorded.
     * Each row = 1 unique visitor on 1 unique day.
     * Persistent across server restarts and migrations (stored in PostgreSQL).
     */
    public long getTotalVisitorCount() {
        return dailyVisitRepository.count();
    }
}
