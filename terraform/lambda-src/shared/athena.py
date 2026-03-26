"""Shared Athena query utility for CUR cost analytics."""

import logging
import time

import boto3

logger = logging.getLogger(__name__)


def run_query(database, query, workgroup, timeout_seconds=30):
    """Execute an Athena query synchronously and return rows as list of dicts.

    On failure (timeout, query error, missing table), logs a warning and returns
    an empty list so callers can gracefully degrade.
    """
    athena = boto3.client("athena")

    try:
        start = athena.start_query_execution(
            QueryString=query,
            QueryExecutionContext={"Database": database},
            WorkGroup=workgroup,
        )
        execution_id = start["QueryExecutionId"]
    except Exception as e:
        logger.warning("Athena start_query_execution failed: %s", e)
        return []

    # Poll until done
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            status = athena.get_query_execution(QueryExecutionId=execution_id)
            state = status["QueryExecution"]["Status"]["State"]
        except Exception as e:
            logger.warning("Athena get_query_execution failed: %s", e)
            return []

        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            reason = status["QueryExecution"]["Status"].get(
                "StateChangeReason", "unknown"
            )
            logger.warning("Athena query %s: %s", state, reason)
            return []

        time.sleep(1)
    else:
        logger.warning("Athena query timed out after %ds", timeout_seconds)
        try:
            athena.stop_query_execution(QueryExecutionId=execution_id)
        except Exception:
            pass
        return []

    # Fetch results with pagination
    rows = []
    columns = None
    next_token = None

    while True:
        kwargs = {"QueryExecutionId": execution_id}
        if next_token:
            kwargs["NextToken"] = next_token

        try:
            result = athena.get_query_results(**kwargs)
        except Exception as e:
            logger.warning("Athena get_query_results failed: %s", e)
            return rows

        result_set = result["ResultSet"]

        if columns is None:
            columns = [
                col["Name"] for col in result_set["ResultSetMetadata"]["ColumnInfo"]
            ]
            # First page includes the header row — skip it
            data_rows = result_set["Rows"][1:]
        else:
            data_rows = result_set["Rows"]

        for row in data_rows:
            values = [d.get("VarCharValue", "") for d in row["Data"]]
            rows.append(dict(zip(columns, values)))

        next_token = result.get("NextToken")
        if not next_token:
            break

    return rows
