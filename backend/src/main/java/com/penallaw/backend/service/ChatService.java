package com.penallaw.backend.service;

import com.penallaw.backend.client.AiServiceClient;
import com.penallaw.backend.dto.ChatDTOs;
import com.penallaw.backend.entity.ChatMessage;
import com.penallaw.backend.entity.ChatSession;
import com.penallaw.backend.entity.User;
import com.penallaw.backend.repository.ChatMessageRepository;
import com.penallaw.backend.repository.ChatSessionRepository;
import com.penallaw.backend.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
@Slf4j
public class ChatService {

    private final ChatSessionRepository sessionRepository;
    private final ChatMessageRepository messageRepository;
    private final UserRepository userRepository;
    private final AiServiceClient aiServiceClient;

    // ── SESSION HELPERS ──────────────────────────────────────────

    private ChatDTOs.SessionResponse toSessionResponse(ChatSession s) {
        return new ChatDTOs.SessionResponse(s.getId(), s.getMode(), s.getTitle(), s.getCreatedAt());
    }

    private ChatDTOs.MessageResponse toMessageResponse(ChatMessage m) {
        return new ChatDTOs.MessageResponse(
                m.getId(), m.getRole(), m.getContent(),
                m.getExtractedFacts(), m.getMappedLaws(), m.getCreatedAt()
        );
    }

    /** Generate a short title from the first user message (max 60 chars). */
    private String generateTitle(String firstMessage) {
        if (firstMessage == null || firstMessage.isBlank()) return "Phiên mới";
        String t = firstMessage.trim().replaceAll("\\s+", " ");
        return t.length() <= 60 ? t : t.substring(0, 57) + "...";
    }

    // ── GUEST SESSIONS (no login required) ──────────────────────

    @Transactional
    public ChatDTOs.SessionResponse createGuestSession(String guestId, ChatDTOs.CreateSessionRequest request) {
        String mode = (request != null && request.mode() != null) ? request.mode() : "neutral";
        LocalDateTime now = LocalDateTime.now();
        ChatSession session = ChatSession.builder()
                .guestId(guestId)
                .mode(mode)
                .title("Phiên mới")
                .createdAt(now)
                .updatedAt(now)
                .build();
        session = sessionRepository.save(session);
        return toSessionResponse(session);
    }

    @Transactional(readOnly = true)
    public List<ChatDTOs.SessionResponse> getGuestSessions(String guestId) {
        return sessionRepository.findByGuestIdOrderByCreatedAtDesc(guestId)
                .stream().map(this::toSessionResponse).collect(Collectors.toList());
    }

    // ── AUTHENTICATED SESSIONS ───────────────────────────────────

    @Transactional
    public ChatDTOs.SessionResponse createSession(String userEmail, ChatDTOs.CreateSessionRequest request) {
        User user = userRepository.findByEmail(userEmail)
                .orElseThrow(() -> new UsernameNotFoundException("User not found: " + userEmail));
        String mode = (request != null && request.mode() != null) ? request.mode() : "neutral";
        LocalDateTime now = LocalDateTime.now();
        ChatSession session = ChatSession.builder()
                .user(user).mode(mode).title("Phiên mới")
                .createdAt(now)
                .updatedAt(now)
                .build();
        session = sessionRepository.save(session);
        return toSessionResponse(session);
    }

    @Transactional(readOnly = true)
    public List<ChatDTOs.SessionResponse> getUserSessions(String userEmail) {
        User user = userRepository.findByEmail(userEmail)
                .orElseThrow(() -> new UsernameNotFoundException("User not found: " + userEmail));
        return sessionRepository.findByUserIdOrderByCreatedAtDesc(user.getId())
                .stream().map(this::toSessionResponse).collect(Collectors.toList());
    }

    // ── MESSAGE SENDING ──────────────────────────────────────────

    @Transactional
    public ChatDTOs.MessageResponse sendMessage(UUID sessionId, ChatDTOs.SendMessageRequest request) {
        ChatSession session = sessionRepository.findById(sessionId)
                .orElseThrow(() -> new RuntimeException("Session not found: " + sessionId));

        // Load prior conversation history for context
        List<ChatMessage> history = messageRepository.findBySessionIdOrderByCreatedAtAsc(sessionId);

        // Auto-generate title on first message
        if (history.isEmpty() && (session.getTitle() == null || session.getTitle().equals("Phiên mới"))) {
            session.setTitle(generateTitle(request.content()));
            sessionRepository.save(session);
        }

        // Save user message
        ChatMessage userMessage = ChatMessage.builder()
                .session(session).role("user").content(request.content())
                .createdAt(LocalDateTime.now())
                .build();
        messageRepository.save(userMessage);

        // Build conversation history list for AI context
        List<Map<String, String>> conversationHistory = history.stream()
                .map(m -> Map.of("role", m.getRole(), "content", m.getContent()))
                .collect(Collectors.toList());

        // Call AI service with history
        String role = (request.role() != null) ? request.role() : session.getMode();
        log.info("Calling AI service for session {} with role {}, history size {}", sessionId, role, conversationHistory.size());

        AiServiceClient.PredictResponse aiResponse;
        try {
            aiResponse = aiServiceClient.predict(request.content(), role, request.rebuttalAgainst(), conversationHistory);
        } catch (Exception e) {
            log.error("AI service error: {}", e.getMessage());
            throw new RuntimeException("Dịch vụ AI không khả dụng. Vui lòng thử lại sau. (" + e.getMessage() + ")");
        }

        // Save AI message — wrap with explicit try-catch for clear error logging
        ChatMessage aiMessage;
        try {
            aiMessage = ChatMessage.builder()
                    .session(session).role("assistant")
                    .content(aiResponse.result())
                    .extractedFacts(aiResponse.extractedFacts())
                    .mappedLaws(aiResponse.mappedLaws())
                    .createdAt(LocalDateTime.now())
                    .build();
            aiMessage = messageRepository.save(aiMessage);
        } catch (Exception e) {
            log.error("CRITICAL: Failed to save AI message to DB for session {}. Error: {}", sessionId, e.getMessage(), e);
            // Return a response anyway so the user can still see the AI result
            return new ChatDTOs.MessageResponse(
                    null, "assistant", aiResponse.result(),
                    aiResponse.extractedFacts(), aiResponse.mappedLaws(), null
            );
        }

        return new ChatDTOs.MessageResponse(
                aiMessage.getId(), "assistant", aiResponse.result(),
                aiResponse.extractedFacts(), aiResponse.mappedLaws(), aiMessage.getCreatedAt()
        );
    }

    // ── HISTORY ──────────────────────────────────────────────────

    @Transactional(readOnly = true)
    public ChatDTOs.ConversationHistoryResponse getHistory(UUID sessionId) {
        ChatSession session = sessionRepository.findById(sessionId)
                .orElseThrow(() -> new RuntimeException("Session not found: " + sessionId));
        List<ChatMessage> messages = messageRepository.findBySessionIdOrderByCreatedAtAsc(sessionId);
        return new ChatDTOs.ConversationHistoryResponse(
                sessionId, session.getMode(), session.getTitle(),
                messages.stream().map(this::toMessageResponse).collect(Collectors.toList())
        );
    }

    // ── DELETE ───────────────────────────────────────────────────

    @Transactional
    public void deleteSession(UUID sessionId) {
        if (!sessionRepository.existsById(sessionId)) {
            throw new RuntimeException("Session not found: " + sessionId);
        }
        sessionRepository.deleteById(sessionId);
    }
}
