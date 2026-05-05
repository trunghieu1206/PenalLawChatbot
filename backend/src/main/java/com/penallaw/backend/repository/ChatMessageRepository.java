package com.penallaw.backend.repository;

import com.penallaw.backend.entity.ChatMessage;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.UUID;

@Repository
public interface ChatMessageRepository extends JpaRepository<ChatMessage, UUID> {
    List<ChatMessage> findBySessionIdOrderByCreatedAtAsc(UUID sessionId);

    /** All AI responses that carry extracted facts (i.e., a new_case flow was executed). */
    @Query("SELECT m FROM ChatMessage m WHERE m.role = 'assistant' AND m.extractedFacts IS NOT NULL")
    List<ChatMessage> findAssistantMessagesWithFacts();
}
