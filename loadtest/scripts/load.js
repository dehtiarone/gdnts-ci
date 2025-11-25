// k6 Load Test Script for gdnts CI Pipeline
// Generates randomized HTTPS traffic to foo.localhost and bar.localhost
// Duration: 4 minutes with variable VU stages

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

// Custom metrics
const errorRate = new Rate('errors');
const fooLatency = new Trend('foo_request_duration');
const barLatency = new Trend('bar_request_duration');

// Test configuration
export const options = {
  // 4-minute test
  stages: [
    { duration: '30s', target: 20 },   // Ramp up to 20 VUs
    { duration: '30s', target: 40 },   // Ramp up to 40 VUs
    { duration: '30s', target: 80 },   // Ramp up to 80 VUs
    { duration: '30s', target: 160 },   // Ramp up to 160 VUs
    { duration: '30s', target: 160 },   // Stay at 160 VUs
    { duration: '30s', target: 80 },    // Ramp down to 80 VUs
    { duration: '1m', target: 0 },    // Ramp down to 0
  ],

  // Thresholds for pass/fail
  thresholds: {
    'http_req_duration': ['p(95)<500'],     // 95% of requests under 500ms
    'http_req_failed': ['rate<0.01'],       // Less than 1% failures
    'errors': ['rate<0.01'],                // Custom error rate
    'foo_request_duration': ['p(95)<500'],  // Foo-specific latency
    'bar_request_duration': ['p(95)<500'],  // Bar-specific latency
  },

  // Skip TLS verification for self-signed certificates
  insecureSkipTLSVerify: true,

  // Tags for Prometheus metrics
  tags: {
    testName: 'gdnts-load-test',
  },
};

// Base URL - use environment variable or default (HTTPS on port 8443)
const BASE_URL = __ENV.BASE_URL || 'https://127.0.0.1:8443';

// Host configurations
const hosts = [
  { name: 'foo', host: 'foo.localhost', expected: 'foo' },
  { name: 'bar', host: 'bar.localhost', expected: 'bar' },
];

// Random selection helper
function randomItem(items) {
  return items[Math.floor(Math.random() * items.length)];
}

// Main test function
export default function() {
  // Randomly select a host
  const target = randomItem(hosts);

  // Make HTTPS request with Host header
  const response = http.get(`${BASE_URL}/`, {
    headers: {
      'Host': target.host,
    },
    tags: {
      endpoint: target.name,
    },
  });

  // Record latency for specific endpoint
  if (target.name === 'foo') {
    fooLatency.add(response.timings.duration);
  } else {
    barLatency.add(response.timings.duration);
  }

  // Validate response
  const success = check(response, {
    'status is 200': (r) => r.status === 200,
    'response contains expected text': (r) => r.body.trim() === target.expected,
    'response time < 500ms': (r) => r.timings.duration < 500,
  });

  // Track errors
  errorRate.add(!success);

  // Random sleep between 100ms and 500ms to simulate realistic traffic
  sleep(0.1 + Math.random() * 0.4);
}

// Setup function - runs once before the test
export function setup() {
  console.log('Starting load test...');
  console.log(`Base URL: ${BASE_URL}`);
  console.log('Targets: foo.localhost, bar.localhost');

  // Verify HTTPS connectivity before starting
  const fooCheck = http.get(`${BASE_URL}/`, {
    headers: { 'Host': 'foo.localhost' },
  });

  const barCheck = http.get(`${BASE_URL}/`, {
    headers: { 'Host': 'bar.localhost' },
  });

  if (fooCheck.status !== 200 || barCheck.status !== 200) {
    console.warn('Warning: Initial connectivity check failed');
    console.log(`foo status: ${fooCheck.status}`);
    console.log(`bar status: ${barCheck.status}`);
  }

  return {
    startTime: new Date().toISOString(),
  };
}

// Teardown function - runs once after the test
export function teardown(data) {
  console.log('Load test completed!');
  console.log(`Started at: ${data.startTime}`);
  console.log(`Ended at: ${new Date().toISOString()}`);
}

// Handle summary - custom summary output
export function handleSummary(data) {
  return {
    'stdout': textSummary(data, { indent: ' ', enableColors: true }),
    'reports/output/k6-summary.json': JSON.stringify(data, null, 2),
  };
}

// Text summary helper
function textSummary(data, options) {
  const indent = options.indent || '';

  let output = '\n';
  output += `${indent}=== Load Test Summary ===\n\n`;

  // Metrics summary
  if (data.metrics) {
    output += `${indent}HTTP Requests:\n`;
    if (data.metrics.http_reqs) {
      output += `${indent}  Total: ${data.metrics.http_reqs.values.count}\n`;
      output += `${indent}  Rate: ${data.metrics.http_reqs.values.rate.toFixed(2)}/s\n`;
    }

    output += `\n${indent}Response Times:\n`;
    if (data.metrics.http_req_duration) {
      const d = data.metrics.http_req_duration.values;
      output += `${indent}  Avg: ${d.avg.toFixed(2)}ms\n`;
      output += `${indent}  Min: ${d.min.toFixed(2)}ms\n`;
      output += `${indent}  Max: ${d.max.toFixed(2)}ms\n`;
      output += `${indent}  P95: ${d['p(95)'].toFixed(2)}ms\n`;
    }

    output += `\n${indent}Error Rate:\n`;
    if (data.metrics.errors) {
      output += `${indent}  Rate: ${(data.metrics.errors.values.rate * 100).toFixed(2)}%\n`;
    }
  }

  // Thresholds
  if (data.root_group && data.root_group.checks) {
    output += `\n${indent}Checks:\n`;
    for (const check of Object.values(data.root_group.checks)) {
      const passed = check.passes;
      const failed = check.fails;
      const total = passed + failed;
      const rate = total > 0 ? ((passed / total) * 100).toFixed(1) : '0.0';
      output += `${indent}  ${check.name}: ${rate}% (${passed}/${total})\n`;
    }
  }

  return output;
}
