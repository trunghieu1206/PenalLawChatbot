package com.penallaw.backend.repository;

import com.penallaw.backend.entity.ChatSession;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface ChatSessionRepository extends JpaRepository<ChatSession, UUID> {
    List<ChatSession> findByUserIdOrderByCreatedAtDesc(UUID userId);
    List<ChatSession> findByGuestIdOrderByCreatedAtDesc(String guestId);
}
