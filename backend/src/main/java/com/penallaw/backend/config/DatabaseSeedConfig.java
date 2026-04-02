package com.penallaw.backend.config;

import com.penallaw.backend.entity.User;
import com.penallaw.backend.repository.UserRepository;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.CommandLineRunner;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.crypto.password.PasswordEncoder;

@Configuration
@Slf4j
public class DatabaseSeedConfig {

    /**
     * Optionally seed the database with a test user on application startup.
     * This can be disabled by setting: app.seed-database=false
     */
    @Bean
    public CommandLineRunner seedDatabase(UserRepository userRepository, PasswordEncoder passwordEncoder) {
        return args -> {
            // Only seed if the test user doesn't already exist
            if (!userRepository.existsByEmail("hieu@gmail.com")) {
                User testUser = User.builder()
                        .email("hieu@gmail.com")
                        .passwordHash(passwordEncoder.encode("hieu"))
                        .fullName("Hiệu Test User")
                        .role("user")
                        .isActive(true)
                        .build();
                userRepository.save(testUser);
                log.info("✅ Test user 'hieu@gmail.com' (password: 'hieu') created successfully");
            } else {
                log.debug("Test user 'hieu@gmail.com' already exists, skipping seed");
            }
        };
    }
}
