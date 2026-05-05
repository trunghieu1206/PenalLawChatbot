package com.penallaw.backend.repository;

import com.penallaw.backend.entity.DailyVisit;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.UUID;

@Repository
public interface DailyVisitRepository extends JpaRepository<DailyVisit, UUID> {

    /** Check if this visitor has already been counted today. */
    boolean existsByVisitorIdAndVisitDate(String visitorId, LocalDate visitDate);
}
