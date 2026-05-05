package com.penallaw.backend.repository;

import com.penallaw.backend.entity.ChatSession;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.List;
import java.util.UUID;

@Repository
public interface ChatSessionRepository extends JpaRepository<ChatSession, UUID> {
    List<ChatSession> findByUserIdOrderByCreatedAtDesc(UUID userId);
    List<ChatSession> findByGuestIdOrderByCreatedAtDesc(String guestId);
    long countByUserIdAndCreatedAtAfter(UUID userId, LocalDateTime after);
    long countByGuestIdAndCreatedAtAfter(String guestId, LocalDateTime after);

    /** Total session count per registered user (all time). */
    @Query("SELECT s.user, COUNT(s) FROM ChatSession s WHERE s.user IS NOT NULL GROUP BY s.user")
    List<Object[]> findUserSessionCounts();

    /** Session count per registered user since a given timestamp (used for \"today\" counts). */
    @Query("SELECT s.user, COUNT(s) FROM ChatSession s WHERE s.user IS NOT NULL AND s.createdAt >= :startOfDay GROUP BY s.user")
    List<Object[]> findUserSessionCountsToday(@Param("startOfDay") LocalDateTime startOfDay);
}
