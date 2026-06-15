package com.penallaw.backend.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.penallaw.backend.client.AiServiceClient;
import com.penallaw.backend.dto.ChatDTOs;
import com.penallaw.backend.entity.ChatMessage;
import com.penallaw.backend.entity.ChatSession;
import com.penallaw.backend.entity.User;
import com.penallaw.backend.repository.ChatMessageRepository;
import com.penallaw.backend.repository.ChatSessionRepository;
import com.penallaw.backend.repository.UserRepository;
import com.penallaw.backend.exception.RateLimitException;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.security.core.userdetails.UsernameNotFoundException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.nio.charset.StandardCharsets;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.HashMap;
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
    private final ObjectMapper objectMapper;

    // ── SESSION HELPERS ──────────────────────────────────────────

    private ChatDTOs.SessionResponse toSessionResponse(ChatSession s) {
        return new ChatDTOs.SessionResponse(s.getId(), s.getMode(), s.getTitle(), s.getCreatedAt());
    }

    private ChatDTOs.MessageResponse toMessageResponse(ChatMessage m) {
        return new ChatDTOs.MessageResponse(
                m.getId(), m.getRole(), m.getContent(),
                m.getExtractedFacts(), m.getMappedLaws(), m.getSentencingData(), m.getCreatedAt()
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
        // Limit check: 3 sessions per day for guest users
        LocalDateTime startOfToday = LocalDateTime.of(LocalDate.now(), LocalTime.MIDNIGHT);
        long sessionsToday = sessionRepository.countByGuestIdAndCreatedAtAfter(guestId, startOfToday);
        if (sessionsToday >= 3) {
            throw new RateLimitException("Khách truy cập được giới hạn 3 vụ án mỗi ngày. Vui lòng đăng nhập để có giới hạn cao hơn.");
        }

        String mode = (request != null && request.mode() != null) ? request.mode() : "neutral";
        LocalDateTime now = LocalDateTime.now();
        ChatSession session = ChatSession.builder()
                .guestId(guestId).mode(mode).title("Phiên mới")
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

        // Build conversation history list for AI context.
        // For assistant messages, include mapped_laws so the AI service can
        // restore law context on follow-up turns (avoids re-running the full pipeline).
        List<Map<String, Object>> conversationHistory = history.stream()
                .map(m -> {
                    Map<String, Object> entry = new HashMap<>();
                    entry.put("role", m.getRole());
                    entry.put("content", m.getContent());
                    if ("assistant".equals(m.getRole()) && m.getMappedLaws() != null) {
                        entry.put("mapped_laws", m.getMappedLaws());
                    }
                    return entry;
                })
                .collect(Collectors.toList());

        // Call AI service with history
        String role = (request.role() != null) ? request.role() : session.getMode();
        log.info("Calling AI service for session {} with role {}, history size {}", sessionId, role, conversationHistory.size());

        AiServiceClient.PredictResponse aiResponse;
        try {
            aiResponse = aiServiceClient.predict(request.content(), role, request.rebuttalAgainst(), conversationHistory, sessionId.toString());
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
                    .sentencingData(aiResponse.sentencingData())
                    .createdAt(LocalDateTime.now())
                    .build();
            aiMessage = messageRepository.save(aiMessage);
        } catch (Exception e) {
            log.error("CRITICAL: Failed to save AI message to DB for session {}. Error: {}", sessionId, e.getMessage(), e);
            // Return a response anyway so the user can still see the AI result
            return new ChatDTOs.MessageResponse(
                    null, "assistant", aiResponse.result(),
                    aiResponse.extractedFacts(), aiResponse.mappedLaws(), aiResponse.sentencingData(), null
            );
        }

        return new ChatDTOs.MessageResponse(
                aiMessage.getId(), "assistant", aiResponse.result(),
                aiResponse.extractedFacts(), aiResponse.mappedLaws(), aiResponse.sentencingData(), aiMessage.getCreatedAt()
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

    // ── CSV EXPORT ───────────────────────────────────────────────

    /**
     * Export a full session conversation as a UTF-8 BOM CSV byte array.
     *
     * Columns: #, timestamp, role, content, session_mode, session_title,
     *          mapped_laws (JSON), extracted_facts (JSON), sentencing_data (JSON)
     *
     * The UTF-8 BOM (0xEF 0xBB 0xBF) is prepended so that Excel on Windows
     * opens the file with correct Vietnamese character encoding without any
     * manual import steps. Mac/Linux tools ignore the BOM transparently.
     *
     * @param sessionId UUID of the session to export
     * @return raw CSV bytes ready to stream as a file download
     */
    @Transactional(readOnly = true)
    public byte[] exportSessionAsCsv(UUID sessionId) {
        ChatSession session = sessionRepository.findById(sessionId)
                .orElseThrow(() -> new RuntimeException("Session not found: " + sessionId));
        List<ChatMessage> messages = messageRepository.findBySessionIdOrderByCreatedAtAsc(sessionId);

        DateTimeFormatter fmt = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
        String sessionMode  = session.getMode()  != null ? session.getMode()  : "";
        String sessionTitle = session.getTitle() != null ? session.getTitle() : "Phiên mới";

        StringBuilder sb = new StringBuilder();

        // Header row
        sb.append("#,timestamp,role,content,session_mode,session_title,mapped_laws,extracted_facts,sentencing_data\r\n");

        for (int i = 0; i < messages.size(); i++) {
            ChatMessage m = messages.get(i);

            String timestamp = m.getCreatedAt() != null
                    ? fmt.format(m.getCreatedAt()) : "";

            // Structured JSON columns — empty for user rows
            String mappedLawsJson    = "";
            String extractedFactsJson = "";
            String sentencingDataJson = "";

            if ("assistant".equals(m.getRole())) {
                mappedLawsJson     = toJson(m.getMappedLaws());
                extractedFactsJson = toJson(m.getExtractedFacts());
                sentencingDataJson = toJson(m.getSentencingData());
            }

            sb.append(i + 1).append(',');
            sb.append(csvQuote(timestamp)).append(',');
            sb.append(csvQuote(m.getRole())).append(',');
            sb.append(csvQuote(m.getContent())).append(',');
            sb.append(csvQuote(sessionMode)).append(',');
            sb.append(csvQuote(sessionTitle)).append(',');
            sb.append(csvQuote(mappedLawsJson)).append(',');
            sb.append(csvQuote(extractedFactsJson)).append(',');
            sb.append(csvQuote(sentencingDataJson)).append("\r\n");
        }

        // Prepend UTF-8 BOM for Windows Excel compatibility
        byte[] bom  = new byte[]{(byte) 0xEF, (byte) 0xBB, (byte) 0xBF};
        byte[] body = sb.toString().getBytes(StandardCharsets.UTF_8);
        byte[] result = new byte[bom.length + body.length];
        System.arraycopy(bom, 0, result, 0, bom.length);
        System.arraycopy(body, 0, result, bom.length, body.length);
        return result;
    }

    /**
     * Wrap a string value in double-quotes for CSV, escaping internal double-quotes
     * by doubling them (RFC 4180). Null-safe: null → empty quoted cell.
     * Newlines within the value are preserved inside the quoted cell — RFC 4180 allows
     * this and spreadsheet apps (Excel, LibreOffice, Numbers) handle it correctly.
     */
    private String csvQuote(String value) {
        if (value == null) return "\"\"";
        return '"' + value.replace("\"", "\"\"") + '"';
    }

    /** Safely serialise any object to a compact JSON string. Returns "" on failure. */
    private String toJson(Object value) {
        if (value == null) return "";
        try {
            return objectMapper.writeValueAsString(value);
        } catch (JsonProcessingException e) {
            log.warn("Failed to serialise field to JSON for CSV export: {}", e.getMessage());
            return "";
        }
    }
}
