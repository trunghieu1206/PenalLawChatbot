package com.penallaw.backend.service;

import com.penallaw.backend.dto.AdminDTOs;
import com.penallaw.backend.entity.ChatMessage;
import com.penallaw.backend.entity.ChatSession;
import com.penallaw.backend.entity.Feedback;
import com.penallaw.backend.repository.ChatMessageRepository;
import com.penallaw.backend.repository.ChatSessionRepository;
import com.penallaw.backend.repository.FeedbackRepository;
import com.penallaw.backend.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.TreeMap;
import java.util.UUID;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
@Slf4j
public class AdminService {

    private final ChatSessionRepository sessionRepository;
    private final ChatMessageRepository messageRepository;
    private final FeedbackRepository feedbackRepository;
    private final UserRepository userRepository;

    // ── DASHBOARD STATS ──────────────────────────────────────────

    @Transactional(readOnly = true)
    public AdminDTOs.DashboardStats getStats() {

        long totalSessions = sessionRepository.count();
        long totalUsers    = userRepository.count();

        // Sessions that ran the new_case pipeline (AI response has extracted_facts)
        List<ChatMessage> aiMessages = messageRepository.findAssistantMessagesWithFacts();
        long casesProcessed = aiMessages.stream()
                .map(m -> m.getSession().getId())
                .distinct()
                .count();

        // -- Sessions by role --
        Map<String, Long> byRole = sessionRepository.findAll().stream()
                .collect(Collectors.groupingBy(
                        s -> Optional.ofNullable(s.getMode()).orElse("neutral"),
                        Collectors.counting()
                ));

        // -- Cases by province (dia_danh from extracted_facts) --
        Map<String, Long> byProvince = new TreeMap<>();
        for (ChatMessage m : aiMessages) {
            String province = extractField(m.getExtractedFacts(), "dia_danh");
            if (province != null && !province.isBlank()) {
                byProvince.merge(normalizeProvince(province), 1L, Long::sum);
            }
        }

        // -- Cases by crime type (offense_name from mappedLaws[0]) --
        Map<String, Long> byCrimeType = new TreeMap<>();
        for (ChatMessage m : aiMessages) {
            String crimeType = extractCrimeType(m.getMappedLaws());
            if (crimeType != null && !crimeType.isBlank()) {
                byCrimeType.merge(crimeType, 1L, Long::sum);
            }
        }

        // -- Feedback stats --
        long feedbackTotal     = feedbackRepository.count();
        long feedbackCorrect   = feedbackRepository.countByIsCorrectTrue();
        long feedbackIncorrect = feedbackRepository.countByIsCorrectFalse();

        return new AdminDTOs.DashboardStats(
                totalSessions, totalUsers, casesProcessed,
                byRole, byProvince, byCrimeType,
                feedbackTotal, feedbackCorrect, feedbackIncorrect
        );
    }

    // ── FEEDBACK ADMIN LIST ───────────────────────────────────────

    @Transactional(readOnly = true)
    public List<AdminDTOs.FeedbackDetail> getAllFeedback() {
        List<Feedback> feedbacks = feedbackRepository.findAllByOrderByCreatedAtDesc();
        return feedbacks.stream().map(f -> {
            UUID sid = f.getSessionId();

            // Load session conversation for context (skip gracefully if sessionId is missing)
            List<AdminDTOs.MessageSummary> conversation = List.of();
            if (sid != null) {
                conversation = messageRepository.findBySessionIdOrderByCreatedAtAsc(sid)
                        .stream()
                        .map(m -> new AdminDTOs.MessageSummary(
                                m.getId(), m.getRole(), m.getContent(), m.getCreatedAt()))
                        .collect(Collectors.toList());
            }

            // Session metadata
            String sessionMode = (sid != null)
                    ? sessionRepository.findById(sid).map(ChatSession::getMode).orElse("unknown")
                    : "unknown";

            return new AdminDTOs.FeedbackDetail(
                    f.getId(), sid, f.getMessageId(),
                    f.getIsCorrect(), f.getComment(), f.getCreatedAt(),
                    sessionMode, conversation
            );
        }).collect(Collectors.toList());
    }

    // ── SUBMIT FEEDBACK ───────────────────────────────────────────

    @Transactional
    public AdminDTOs.FeedbackResponse submitFeedback(UUID sessionId, UUID messageId,
                                                      boolean isCorrect, String comment) {
        // Upsert: update existing feedback for this message, or create a new record
        Feedback feedback = feedbackRepository.findByMessageId(messageId)
                .orElse(Feedback.builder().sessionId(sessionId).messageId(messageId).build());

        feedback.setIsCorrect(isCorrect);
        feedback.setComment(comment);
        feedback = feedbackRepository.save(feedback);
        return new AdminDTOs.FeedbackResponse(feedback.getId(), "Đã ghi nhận phản hồi. Cảm ơn bạn!");
    }

    // ── HELPERS ───────────────────────────────────────────────────

    @SuppressWarnings("unchecked")
    private String extractField(Map<String, Object> facts, String key) {
        if (facts == null) return null;
        Object val = facts.get(key);
        return val instanceof String ? (String) val : null;
    }

    @SuppressWarnings("unchecked")
    private String extractCrimeType(List<Map<String, Object>> mappedLaws) {
        if (mappedLaws == null || mappedLaws.isEmpty()) return null;
        Object name = mappedLaws.get(0).get("offense_name");
        return name instanceof String ? (String) name : null;
    }

    /** Normalize province strings: trim, title-case common abbreviations. */
    private String normalizeProvince(String raw) {
        if (raw == null) return null;
        String s = raw.trim();
        // Map common abbreviations
        if (s.toLowerCase().contains("hà nội"))        return "Hà Nội";
        if (s.toLowerCase().contains("hồ chí minh") || s.toLowerCase().contains("tp.hcm") || s.toLowerCase().contains("tp hcm")) return "TP. Hồ Chí Minh";
        if (s.toLowerCase().contains("đà nẵng"))       return "Đà Nẵng";
        if (s.toLowerCase().contains("cần thơ"))       return "Cần Thơ";
        if (s.toLowerCase().contains("hải phòng"))     return "Hải Phòng";
        return s;
    }
}
