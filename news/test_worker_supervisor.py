import io
from unittest.mock import Mock, patch
from django.test import SimpleTestCase, TestCase
import worker_supervisor


class WorkerRecoveryTests(SimpleTestCase):
    def run_supervisor(self, first):
        second = Mock()
        second.poll.return_value=None
        children=[first,second]
        handlers={}
        def register(sig, handler):
            handlers[sig]=handler
            return None
        calls=0
        def launch(*args, **kwargs):
            nonlocal calls
            calls+=1
            if calls==2:
                handlers[worker_supervisor.signal.SIGTERM]()
            return children[calls-1]
        with patch.object(worker_supervisor.signal,'signal',side_effect=register), patch.object(worker_supervisor.subprocess,'Popen',side_effect=launch) as popen, patch.object(worker_supervisor.time,'sleep'), patch('sys.stdout',new_callable=io.StringIO):
            worker_supervisor.supervise(timeout=120,retry_delay=0)
        self.assertEqual(popen.call_count,2)
        return first

    def test_crashed_process_is_restarted_automatically(self):
        first=Mock(); first.poll.return_value=1
        self.run_supervisor(first)
        first.kill.assert_not_called()

    def test_stalled_process_is_reaped_before_replacement(self):
        first=Mock();first.poll.return_value=None
        with patch.object(worker_supervisor.time,'time',return_value=10**12):
            self.run_supervisor(first)
        first.terminate.assert_called_once()
        first.wait.assert_called_once()

    def test_timeout_does_not_kill_valid_small_work_batches(self):
        with self.assertRaises(ValueError):
            worker_supervisor.supervise(timeout=5)


class ContinuousWorkerTests(TestCase):
    def test_photo_failure_does_not_prevent_worker_heartbeat(self):
        from news.management.commands.run_news_worker import Command
        from news.management.commands import run_news_worker as worker
        from news.models import AutomationSettings
        from django.utils import timezone
        cfg=AutomationSettings.objects.create(pk=1,enabled=False)
        before=timezone.now()
        with patch.object(worker,'tick_images',side_effect=RuntimeError('private photo detail')), patch.object(worker,'work_plan',return_value={'publish':False,'retry':False,'prepare':False,'trends':False,'wait':30}):
            stderr=io.StringIO()
            Command(stdout=io.StringIO(),stderr=stderr).handle(once=True)
        cfg.refresh_from_db()
        self.assertGreaterEqual(cfg.worker_seen,before)
        self.assertFalse(cfg.enabled)
        self.assertNotIn('private photo detail',stderr.getvalue())

    def test_worker_retries_after_error_and_updates_supervisor_progress(self):
        import os
        from pathlib import Path
        import tempfile
        from news.management.commands.run_news_worker import Command
        from news.management.commands import run_news_worker as worker
        handlers={}
        def register(sig,handler):
            handlers[sig]=handler
        with tempfile.TemporaryDirectory() as directory:
            progress=Path(directory)/'progress'
            with patch.dict(os.environ,{'NEWS_WORKER_PROGRESS_FILE':str(progress)}), patch.object(worker.signal,'signal',side_effect=register), patch.object(worker,'tick',side_effect=[RuntimeError('private connection detail'),None]) as tick, patch.object(worker,'tick_article',return_value=None) as article, patch.object(worker,'tick_retries',return_value=None), patch.object(worker,'tick_images',return_value=None), patch.object(worker,'work_plan',return_value={'publish':True,'retry':False,'prepare':True,'trends':False,'wait':30}), patch.object(worker.connections,'close_all') as close:
                def sleep(_):
                    if tick.call_count>=2:
                        handlers[worker.signal.SIGTERM]()
                with patch.object(worker.time,'sleep',side_effect=sleep):
                    stderr=io.StringIO()
                    Command(stdout=io.StringIO(),stderr=stderr).handle(once=False)
                self.assertEqual(tick.call_count,2)
                article.assert_called_once()
                close.assert_called_once()
                self.assertTrue(progress.exists())
                self.assertNotIn('private connection detail',stderr.getvalue())
