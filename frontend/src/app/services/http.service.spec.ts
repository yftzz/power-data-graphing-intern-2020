// Copyright 2020 Google LLC
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
// =============================================================================

import { TestBed } from '@angular/core/testing';

import { HttpService, STRATEGY } from './http.service';
import { HttpClient } from '@angular/common/http';
import { from } from 'rxjs';

describe('HttpService', () => {
  let service: HttpService;
  let httpClientSpy: { get: jasmine.Spy };

  beforeEach(() => {
    httpClientSpy = jasmine.createSpyObj('HttpClient', ['get']);
    TestBed.configureTestingModule({
      providers: [
        HttpService,
        { provide: HttpClient, useValue: httpClientSpy },
      ],
    });
    service = new HttpService(<any>httpClientSpy);
  });

  it('HTTP service should be created', () => {
    expect(service).toBeTruthy();
  });

  it('getRecords should return list of records', () => {
    const expectedRecords = [
      [1, 1, 'a'],
      [2, 2, 's'],
      [3, 3, 'd'],
    ];
    httpClientSpy.get.and.returnValue(from([expectedRecords]));

    service.getRecords('data', STRATEGY.MAX, null).subscribe((records) => {
      expect(records).toEqual(expectedRecords);
    });
  });
});
