package com.penallaw.backend.service;

import com.penallaw.backend.entity.SiteStats;
import com.penallaw.backend.repository.SiteStatsRepository;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.concurrent.atomic.AtomicLong;

@Service
@RequiredArgsConstructor
@Slf4j
public class VisitorTrackingService {

    private final SiteStatsRepository repository;
    private final AtomicLong pendingCount = new AtomicLong(0);

    /** Initialize the in-memory counter from the database on startup. */
    @PostConstruct
    public void init() {
        log.info("Initializing visitor tracking...");
    }

    /** Increment the in-memory counter. This is extremely fast and has zero DB overhead. */
    public void increment() {
        pendingCount.incrementAndGet();
    }

    /** Returns the total count (DB + current in-memory pending). */
    public long getTotalVisitorCount() {
        long dbCount = repository.findById("global").map(SiteStats::getVisitorCount).orElse(0L);
        return dbCount + pendingCount.get();
    }

    /** 
     * Periodically flushes the pending count to the database.
     * Runs every 5 minutes. Adjust fixedRate as needed.
     */
    @Scheduled(fixedRate = 300000) 
    @Transactional
    public void flushToDatabase() {
        long countToFlush = pendingCount.getAndSet(0);
        if (countToFlush > 0) {
            repository.incrementVisitorCount(countToFlush);
            log.debug("Flushed {} visitor hits to database.", countToFlush);
        }
    }
}
