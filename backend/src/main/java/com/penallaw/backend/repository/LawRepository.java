package com.penallaw.backend.repository;

import com.penallaw.backend.entity.Law;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface LawRepository extends JpaRepository<Law, Integer> {

    /**
     * Return ALL versions of an article ordered by effectiveDate DESC (most recent first).
     * The controller partitions and re-orders this list based on crimeDate if provided.
     */
    @Query("""
            SELECT l FROM Law l
            WHERE l.articleNumber = :articleNumber
            ORDER BY l.effectiveDate DESC NULLS LAST
            """)
    List<Law> findAllVersionsByArticleNumber(@Param("articleNumber") String articleNumber);

    /**
     * Find a specific article by article number and source (for disambiguation).
     * When article numbers can vary across different BLHS versions (e.g., Art 249 in 2009 vs 2025),
     * this query allows specifying the exact source to get the correct article.
     */
    @Query("""
            SELECT l FROM Law l
            WHERE l.articleNumber = :articleNumber AND l.source = :source
            ORDER BY l.effectiveDate DESC NULLS LAST
            LIMIT 1
            """)
    Law findByArticleNumberAndSource(@Param("articleNumber") String articleNumber, @Param("source") String source);
}
