package com.penallaw.backend.repository;

import com.penallaw.backend.entity.SiteStats;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

@Repository
public interface SiteStatsRepository extends JpaRepository<SiteStats, String> {

    @Modifying
    @Query("UPDATE SiteStats s SET s.visitorCount = s.visitorCount + :increment WHERE s.id = 'global'")
    void incrementVisitorCount(long increment);
}
