package com.penallaw.backend.repository;

import com.penallaw.backend.entity.Feedback;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface FeedbackRepository extends JpaRepository<Feedback, UUID> {
    List<Feedback> findBySessionIdOrderByCreatedAtDesc(UUID sessionId);
    List<Feedback> findAllByOrderByCreatedAtDesc();
    long countByIsCorrectTrue();
    long countByIsCorrectFalse();
    boolean existsBySessionIdAndMessageId(UUID sessionId, UUID messageId);
}
