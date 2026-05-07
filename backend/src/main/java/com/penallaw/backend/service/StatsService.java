package com.penallaw.backend.service;

import com.penallaw.backend.dto.StatsDTOs;
import com.penallaw.backend.entity.ChatMessage;
import com.penallaw.backend.entity.ChatSession;
import com.penallaw.backend.repository.ChatMessageRepository;
import com.penallaw.backend.repository.ChatSessionRepository;
import com.penallaw.backend.repository.DailyVisitRepository;
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
import java.util.stream.Collectors;

/**
 * Service responsible for aggregating public-facing system statistics.
 * These metrics are accessible to any authenticated user (not admin-only).
 *
 * Responsibilities:
 *   - Aggregate session, user, and case counts for the dashboard.
 *   - Compute breakdown statistics by role, province, and crime type.
 *   - Include feedback summary counts (correct/incorrect) for transparency.
 */
@Service
@RequiredArgsConstructor
@Slf4j
public class StatsService {

    private final ChatSessionRepository sessionRepository;
    private final ChatMessageRepository messageRepository;
    private final FeedbackRepository    feedbackRepository;
    private final UserRepository        userRepository;
    private final DailyVisitRepository  dailyVisitRepository;

    /**
     * Build the full public dashboard statistics object.
     * Aggregates data from sessions, messages, users, visitors, and feedback.
     *
     * @return StatsDTOs.DashboardStats with all metrics populated.
     */
    @Transactional(readOnly = true)
    public StatsDTOs.DashboardStats getStats() {

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

        // -- Feedback summary --
        long feedbackTotal     = feedbackRepository.count();
        long feedbackCorrect   = feedbackRepository.countByIsCorrectTrue();
        long feedbackIncorrect = feedbackRepository.countByIsCorrectFalse();

        return new StatsDTOs.DashboardStats(
                totalSessions, totalUsers, casesProcessed,
                dailyVisitRepository.count(),
                byRole, byProvince, byCrimeType,
                feedbackTotal, feedbackCorrect, feedbackIncorrect
        );
    }

    // ── PRIVATE HELPERS ───────────────────────────────────────────

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

    /** Normalize province strings: trim and map common abbreviations. */
    private String normalizeProvince(String raw) {
        if (raw == null) return null;
        String s = raw.trim();
        if (s.toLowerCase().contains("hà nội"))        return "Hà Nội";
        if (s.toLowerCase().contains("hồ chí minh") || s.toLowerCase().contains("tp.hcm") || s.toLowerCase().contains("tp hcm")) return "TP. Hồ Chí Minh";
        if (s.toLowerCase().contains("đà nẵng"))       return "Đà Nẵng";
        if (s.toLowerCase().contains("cần thơ"))       return "Cần Thơ";
        if (s.toLowerCase().contains("hải phòng"))     return "Hải Phòng";
        return s;
    }
}
