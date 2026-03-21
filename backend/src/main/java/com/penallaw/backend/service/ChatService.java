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

import java.util.List;
import java.util.Map;
import java.util.UUID;

@Service
@RequiredArgsConstructor
@Slf4j
public class ChatService {

    private final ChatSessionRepository sessionRepository;
    private final ChatMessageRepository messageRepository;
    private final UserRepository userRepository;
    private final AiServiceClient aiServiceClient;

    @Transactional
    public ChatDTOs.SessionResponse createSession(String userEmail, ChatDTOs.CreateSessionRequest request) {
        User user = userRepository.findByEmail(userEmail)
                .orElseThrow(() -> new UsernameNotFoundException("User not found: " + userEmail));

        String mode = (request.mode() != null) ? request.mode() : "neutral";
        ChatSession session = ChatSession.builder()
                .user(user)
                .mode(mode)
                .build();
        session = sessionRepository.save(session);
        return new ChatDTOs.SessionResponse(session.getId(), session.getMode(), session.getCreatedAt());
    }

    @Transactional
    public ChatDTOs.MessageResponse sendMessage(String userEmail, UUID sessionId, ChatDTOs.SendMessageRequest request) {
        ChatSession session = sessionRepository.findById(sessionId)
                .orElseThrow(() -> new RuntimeException("Session not found: " + sessionId));

        // Verify session belongs to user
        if (!session.getUser().getEmail().equals(userEmail)) {
            throw new SecurityException("Access denied to session: " + sessionId);
        }

        // Save user message
        ChatMessage userMessage = ChatMessage.builder()
                .session(session)
                .role("user")
                .content(request.content())
                .build();
        messageRepository.save(userMessage);

        // Call AI service
        String role = (request.role() != null) ? request.role() : session.getMode();
        log.info("Calling AI service for session {} with role {}", sessionId, role);

        AiServiceClient.PredictResponse aiResponse;
        try {
            aiResponse = aiServiceClient.predict(request.content(), role, request.rebuttalAgainst());
        } catch (Exception e) {
            log.error("AI service error: {}", e.getMessage());
            throw new RuntimeException("Dịch vụ AI không khả dụng. Vui lòng thử lại sau. (" + e.getMessage() + ")");
        }

        // Save AI message
        ChatMessage aiMessage = ChatMessage.builder()
                .session(session)
                .role("assistant")
                .content(aiResponse.result())
                .extractedFacts(aiResponse.extractedFacts())
                .mappedLaws(aiResponse.mappedLaws())
                .build();
        messageRepository.save(aiMessage);

        return new ChatDTOs.MessageResponse(
                aiMessage.getId(),
                "assistant",
                aiResponse.result(),
                aiResponse.extractedFacts(),
                aiResponse.mappedLaws(),
                aiMessage.getCreatedAt()
        );
    }

    @Transactional(readOnly = true)
    public ChatDTOs.ConversationHistoryResponse getHistory(String userEmail, UUID sessionId) {
        ChatSession session = sessionRepository.findById(sessionId)
                .orElseThrow(() -> new RuntimeException("Session not found: " + sessionId));

        if (!session.getUser().getEmail().equals(userEmail)) {
            throw new SecurityException("Access denied to session: " + sessionId);
        }

        List<ChatMessage> messages = messageRepository.findBySessionIdOrderByCreatedAtAsc(sessionId);
        List<ChatDTOs.MessageResponse> messageDTOs = messages.stream()
                .map(m -> new ChatDTOs.MessageResponse(
                        m.getId(), m.getRole(), m.getContent(),
                        m.getExtractedFacts(), null, m.getCreatedAt()
                ))
                .toList();

        return new ChatDTOs.ConversationHistoryResponse(sessionId, session.getMode(), messageDTOs);
    }

    @Transactional(readOnly = true)
    public List<ChatDTOs.SessionResponse> getUserSessions(String userEmail) {
        User user = userRepository.findByEmail(userEmail)
                .orElseThrow(() -> new UsernameNotFoundException("User not found: " + userEmail));
        return sessionRepository.findByUserIdOrderByCreatedAtDesc(user.getId()).stream()
                .map(s -> new ChatDTOs.SessionResponse(s.getId(), s.getMode(), s.getCreatedAt()))
                .toList();
    }
}
