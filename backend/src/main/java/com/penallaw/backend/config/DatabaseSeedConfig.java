package com.penallaw.backend.config;

import lombok.extern.slf4j.Slf4j;
import org.springframework.context.annotation.Configuration;

/**
 * Database configuration class.
 * 
 * Test user creation has been removed from auto-seed.
 * To add test account manually, use:
 *   - scripts/add_test_user.sh (on server)
 *   - database/init.sql (SQL script)
 *   - or registration page to create accounts
 */
@Configuration
@Slf4j
public class DatabaseSeedConfig {
    // No auto-seeding of test users
}
