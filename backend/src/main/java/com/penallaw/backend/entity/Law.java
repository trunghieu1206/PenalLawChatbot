package com.penallaw.backend.entity;

import jakarta.persistence.*;
import lombok.*;
import org.hibernate.annotations.CreationTimestamp;

import java.time.LocalDate;
import java.time.LocalDateTime;

/**
 * Maps the `laws` table populated by the ingest_laws.py script.
 * Uses ddl-auto=update — Hibernate will NOT re-create columns that already exist.
 */
@Entity
@Table(name = "laws")
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class Law {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Integer id;

    @Column(name = "article_number", length = 20, nullable = false)
    private String articleNumber;

    @Column(length = 500)
    private String title;

    @Column(length = 20)
    private String chapter;

    @Column(columnDefinition = "TEXT", nullable = false)
    private String content;

    @Column(length = 200, nullable = false)
    private String source;

    @Column(name = "effective_date")
    private LocalDate effectiveDate;

    @Column(name = "effective_end_date")
    private LocalDate effectiveEndDate;

    @Column(name = "is_active")
    @Builder.Default
    private Boolean isActive = true;

    @Column
    @Builder.Default
    private Integer version = 1;

    @CreationTimestamp
    @Column(name = "created_at", updatable = false)
    private LocalDateTime createdAt;
}
