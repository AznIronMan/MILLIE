from __future__ import annotations

import unittest

from millie.storage.postgres_safety import (
    UnsafePostgresEndpointError,
    validate_postgres_settings,
)


class PostgresSafetyTest(unittest.TestCase):
    def test_rejects_quarantined_main_cluster_endpoint(self) -> None:
        with self.assertRaises(UnsafePostgresEndpointError) as context:
            validate_postgres_settings(
                {
                    "postgres_host_ip": "10.0.10.81",
                    "postgres_port": "5432",
                    "postgres_database": "millie",
                }
            )

        self.assertIn("quarantined MILLIE endpoint", str(context.exception))
        self.assertIn("10.0.10.81:55432/millie", str(context.exception))

    def test_allows_dedicated_recovery_cluster(self) -> None:
        endpoint = validate_postgres_settings(
            {
                "postgres_host_ip": "10.0.10.81",
                "postgres_port": "55432",
                "postgres_database": "millie",
            }
        )

        self.assertEqual(endpoint, ("10.0.10.81", 55432, "millie"))

    def test_allows_unrelated_local_postgres(self) -> None:
        endpoint = validate_postgres_settings(
            {
                "postgres_host_ip": "127.0.0.1",
                "postgres_port": "5432",
                "postgres_database": "millie",
            }
        )

        self.assertEqual(endpoint, ("127.0.0.1", 5432, "millie"))


if __name__ == "__main__":
    unittest.main()
