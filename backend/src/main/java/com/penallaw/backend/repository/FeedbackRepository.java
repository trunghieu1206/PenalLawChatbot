package com.penallaw.backend.repository;

import com.penallaw.backend.entity.Feedback;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;
import java.util.UUID;

@Repository
public interface FeedbackRepository extends JpaRepository<Feedback, UUID> {
    List<Feedback> findBySessionIdOrderByCreatedAtDesc(UUID sessionId);
    List<Feedback> findAllByOrderByCreatedAtDesc();
    long countByIsCorrectTrue();
    long countByIsCorrectFalse();
    boolean existsBySessionIdAndMessageId(UUID sessionId, UUID messageId);

    /** Efficient upsert lookup — replaces the full-scan in AdminService.submitFeedback. */
    Optional<Feedback> findByMessageId(UUID messageId);
}
