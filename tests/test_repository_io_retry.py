import sqlite3
import unittest
from unittest.mock import MagicMock, patch

from review_agent.repository import ReviewRepository


class RepositoryConnectionRetryTests(unittest.TestCase):
    def error(self,code):
        error=sqlite3.OperationalError('simulated connection setup failure')
        error.sqlite_errorcode=code
        return error

    def test_wal_io_failure_closes_handle_and_recovers(self):
        broken,healthy=MagicMock(),MagicMock()
        def configure(statement):
            if statement=='PRAGMA journal_mode': raise self.error(sqlite3.SQLITE_IOERR)
        broken.execute.side_effect=configure
        with patch('review_agent.repository.sqlite3.connect',side_effect=[broken,healthy]) as connect, \
             patch('review_agent.repository.time.sleep'):
            self.assertIs(ReviewRepository(':memory:').connect(),healthy)
        self.assertEqual(connect.call_count,2)
        broken.close.assert_called_once()
        healthy.close.assert_not_called()

    def test_persistent_io_failure_is_bounded_and_all_handles_close(self):
        handles=[MagicMock() for _ in range(4)]
        for handle in handles: handle.execute.side_effect=self.error(sqlite3.SQLITE_IOERR)
        with patch('review_agent.repository.sqlite3.connect',side_effect=handles) as connect, \
             patch('review_agent.repository.time.sleep'):
            with self.assertRaises(sqlite3.OperationalError): ReviewRepository(':memory:').connect()
        self.assertEqual(connect.call_count,4)
        for handle in handles: handle.close.assert_called_once()

    def test_corruption_is_not_retried(self):
        handle=MagicMock()
        handle.execute.side_effect=self.error(sqlite3.SQLITE_CORRUPT)
        with patch('review_agent.repository.sqlite3.connect',return_value=handle) as connect:
            with self.assertRaises(sqlite3.OperationalError): ReviewRepository(':memory:').connect()
        self.assertEqual(connect.call_count,1)
        handle.close.assert_called_once()

    def test_existing_wal_is_not_reconfigured_for_each_connection(self):
        handle=MagicMock()
        handle.execute.return_value.fetchone.return_value=('wal',)
        with patch('review_agent.repository.sqlite3.connect',return_value=handle):
            self.assertIs(ReviewRepository(':memory:').connect(),handle)
        statements=[call.args[0] for call in handle.execute.call_args_list]
        self.assertIn('PRAGMA journal_mode',statements)
        self.assertNotIn('PRAGMA journal_mode=WAL',statements)


if __name__=='__main__': unittest.main()
