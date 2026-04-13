package com.penallaw.backend.repository;

import com.penallaw.backend.entity.Law;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.List;

@Repository
public interface LawRepository extends JpaRepository<Law, Integer> {

    /**
     * Version-aware lookup: find all versions of an article effective at the given crime date.
     * A law version is applicable when:
     *   effective_date <= crimeDate  AND  (effective_end_date IS NULL OR effective_end_date >= crimeDate)
     * Results ordered newest source first.
     */
    @Query("""
            SELECT l FROM Law l
            WHERE l.articleNumber = :articleNumber
              AND (l.effectiveDate IS NULL OR l.effectiveDate <= :crimeDate)
              AND (l.effectiveEndDate IS NULL OR l.effectiveEndDate >= :crimeDate)
            ORDER BY l.effectiveDate DESC NULLS LAST
            """)
    List<Law> findByArticleNumberAndCrimeDate(
            @Param("articleNumber") String articleNumber,
            @Param("crimeDate") LocalDate crimeDate
    );

    /**
     * Fallback: return all active versions of an article (no date filter).
     * Used when crimeDate is not available.
     */
    @Query("""
            SELECT l FROM Law l
            WHERE l.articleNumber = :articleNumber
              AND l.isActive = true
            ORDER BY l.effectiveDate DESC NULLS LAST
            """)
    List<Law> findActiveByArticleNumber(@Param("articleNumber") String articleNumber);

    /**
     * Return ALL versions of an article regardless of active flag.
     * Useful for showing historical context.
     */
    List<Law> findByArticleNumberOrderByEffectiveDateDesc(String articleNumber);
}
