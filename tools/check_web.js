"use strict";
const assert = require("node:assert/strict");
const {bucketAssessment, buildFilterCommand} = require("../web/app.js");
assert.equal(bucketAssessment(300,900,["1:3","3:1"],"strict",5).bucket,"1:3");
assert.equal(bucketAssessment(900,300,["1:3","3:1"],"strict",5).bucket,"3:1");
assert.equal(bucketAssessment(315,900,["1:3","3:1"],"loose",5).matched,true);
assert.equal(bucketAssessment(316,900,["1:3","3:1"],"loose",5).matched,false);
assert.equal(bucketAssessment(900,315,["1:3","3:1"],"loose",5).matched,true);
assert.equal(bucketAssessment(900,316,["1:3","3:1"],"loose",5).matched,false);
assert.ok(buildFilterCommand("linux","256p",["1:3","3:1"],"loose",5).includes('--aspect-buckets "1:3,3:1"'));
const originalBuckets = ["1:1","2:3","3:2","3:4","4:3","9:16","16:9","21:9"];
assert.ok(buildFilterCommand("linux","256p",originalBuckets,"loose",5).includes('--aspect-buckets "'+originalBuckets.join(',')+'"'));
assert.ok(buildFilterCommand("linux","256p",[...originalBuckets,"1:3","3:1"],"loose",5).includes('--aspect-buckets all'));
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
