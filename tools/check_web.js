"use strict";
const assert = require("node:assert/strict");
const {bucketAssessment, buildFilterCommand} = require("../web/app.js");
for (const [width, height] of [[1050,1000],[1000,1050]]) {
  assert.equal(bucketAssessment(width,height,["1:1"],"loose",5).matched,true);
}
for (const [width, height] of [[1051,1000],[1000,1051]]) {
  assert.equal(bucketAssessment(width,height,["1:1"],"loose",5).matched,false);
}
assert.equal(bucketAssessment(1000,1000,["2:3","3:2"],"loose",50).bucket,"2:3");
assert.equal(bucketAssessment(1400,1000,["3:2","4:3"],"loose",20).bucket,"4:3");
assert.equal(bucketAssessment(1001,1000,["1:1"],"strict",5).matched,false);
assert.equal(bucketAssessment(1000,1000,["1:1"],"loose",0).matched,true);
assert.equal(bucketAssessment(1001,1000,["1:1"],"loose",0).matched,false);
assert.equal(bucketAssessment(1001,1000,["1:1"],"loose",1e-8).matched,false);
assert.ok(buildFilterCommand("windows","256p",["1:1"],"loose",5).includes("--aspect-tolerance 0.05"));
assert.ok(buildFilterCommand("linux","384x256",["2:3","3:2"],"strict",5).includes('--aspect-buckets "2:3,3:2" --aspect-mode strict'));
assert.ok(buildFilterCommand("linux","any",[],"strict",5).endsWith("--aspect-buckets none"));
assert.throws(() => buildFilterCommand("linux","256p;echo injected",[],"loose",5));
assert.throws(() => buildFilterCommand("linux","256p",["1:1"],"loose",-1));
assert.throws(() => buildFilterCommand("linux","256p",["1:1"],"loose",Infinity));
console.log("Browser command generator and exact ratio boundary checks passed.");
