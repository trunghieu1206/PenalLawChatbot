package com.penallaw.backend.config;

import com.penallaw.backend.entity.User;
import com.penallaw.backend.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.ApplicationListener;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

/**
 * Seeds a default admin account (admin / admin) on first startup.
 * Safe to run on every restart — skipped if the account already exists.
 */
@Component
@RequiredArgsConstructor
@Slf4j
public class DataInitializer implements ApplicationListener<ApplicationReadyEvent> {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;

    @Override
    public void onApplicationEvent(ApplicationReadyEvent event) {
        if (!userRepository.existsByEmail("admin")) {
            User admin = User.builder()
                    .email("admin")
                    .passwordHash(passwordEncoder.encode("admin"))
                    .fullName("Administrator")
                    .role("admin")
                    .isActive(true)
                    .build();
            userRepository.save(admin);
            log.info("✅ Default admin account created (email=admin).");
        } else {
            log.debug("Admin account already exists — skipping seed.");
        }
    }
}
