package net.ripe.db.whois.common.dao.jdbc;

import org.junit.jupiter.api.Test;

import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class JdbcRpslObjectDaoStatisticsTest {

    @Test
    void periodIsInclusive() {
        assertTrue(JdbcRpslObjectDao.withinPeriod(
                LocalDate.of(2024, 1, 1),
                LocalDate.of(2024, 1, 1),
                LocalDate.of(2024, 1, 1), false));
        assertFalse(JdbcRpslObjectDao.withinPeriod(
                LocalDate.of(2023, 12, 31),
                LocalDate.of(2024, 1, 1),
                LocalDate.of(2024, 12, 31), false));
        assertFalse(JdbcRpslObjectDao.withinPeriod(
                LocalDate.of(2025, 1, 1),
                LocalDate.of(2024, 1, 1),
                LocalDate.of(2024, 12, 31), false));
    }

    @Test
    void undatedObjectsAreOnlyIncludedWhenRequested() {
        assertTrue(JdbcRpslObjectDao.withinPeriod(null, null, LocalDate.now(), true));
        assertFalse(JdbcRpslObjectDao.withinPeriod(null, null, LocalDate.now(), false));
    }
}
